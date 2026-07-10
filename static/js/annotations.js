function updateAnnotationList() {
    const annotationList = document.getElementById('currentAnnotations');
    annotationList.innerHTML = '';
    
    currentAnnotations.forEach((annotation, index) => {
        const li = document.createElement('li');
        li.className = `annotation-item ${annotation.id === selectedAnnotationId ? 'selected' : ''}`;
        li.dataset.annotationId = annotation.id;
        // ponytail: color validated to #rrggbb before inline style to prevent style injection
        const rawColor = getClassColor(annotation.class);
        const safeColor = /^#[0-9a-fA-F]{6}$/.test(rawColor) ? rawColor : '#ff0000';
        li.innerHTML = `
            <div class="annotation-color" style="background-color: ${safeColor};"></div>
            <span class="annotation-class">${escapeHtml(annotation.class)}</span>
            <div class="annotation-actions">
                <button class="btn btn-small btn-danger delete-annotation-btn" data-index="${index}">
                    <i class="fas fa-trash"></i>
                </button>
            </div>
        `;
        annotationList.appendChild(li);
    });
    
    // 添加事件监听器
    document.querySelectorAll('.annotation-item').forEach((item, index) => {
        // 点击选中标注
        item.addEventListener('click', function() {
            const annotationId = parseInt(this.dataset.annotationId);
            selectedAnnotationId = annotationId;
            updateAnnotationList();
            redrawCanvas();
        });
        
        // 删除按钮事件
        const deleteBtn = item.querySelector('.delete-annotation-btn');
        if (deleteBtn) {
            deleteBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                deleteAnnotation(index);
            });
        }
    });
}

// 获取类别颜色
function deleteAnnotation(index) {
    if (confirm('确定要删除这个标注吗？')) {
        const annotation = currentAnnotations[index];
        // 如果删除的是当前选中的标注，重置选中状态
        if (annotation.id === selectedAnnotationId) {
            selectedAnnotationId = null;
        }
        currentAnnotations.splice(index, 1);
        updateAnnotationListDebounced();
        saveAnnotationsSilent();
        redrawCanvas();
    }
}

// 清除当前标注
function clearCurrentAnnotations() {
    if (currentAnnotations.length === 0) {
        showToast('当前没有标注可清除');
        return;
    }
    
    if (confirm(`确定要清除当前图片的 ${currentAnnotations.length} 个标注吗？`)) {
        currentAnnotations = [];
        selectedAnnotationId = null; // 重置选中状态
        updateAnnotationListDebounced();
        
        // 保存空标注并刷新图片列表
        fetch(`/api/annotations/${currentImage}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(currentAnnotations)
        })
        .then(response => {
            if (!response.ok) {
                return response.json().then(data => {
                    throw new Error(data.error || '保存失败');
                });
            }
            // 刷新图片列表以更新标注状态
            return fetch('/api/images');
        })
        .then(response => response.json())
        .then(data => {
            window.allImages = data.images;
            updateImageList(data.images);
            updateImageCount(data.images.length);
            updateAnnotationProgress(data.images);
        })
        .catch(error => {
            console.error('清除标注失败:', error);
            showToast('清除失败: ' + error.message, 'error');
        });
        
        redrawCanvas();
        showToast('标注已清除');
    }
}

// 保存标注 (静默保存，不显示提示，不跳转)
function saveAnnotationsSilent() {
    if (!currentImage) return;
    
    fetch(`/api/annotations/${currentImage}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(currentAnnotations)
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => {
                throw new Error(data.error || '保存失败');
            });
        }
    })
    .catch(error => {
        console.error('静默保存失败:', error);
        showToast('保存失败: ' + error.message, 'error');
    });
}

// 保存标注 (手动保存，显示提示，可能跳转)
function saveAnnotations() {
    if (!currentImage) {
        showToast('请先选择一张图片', 'warning');
        return;
    }
    
    fetch(`/api/annotations/${currentImage}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(currentAnnotations)
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => {
                throw new Error(data.error || '保存失败');
            });
        }
        return response.json();
    })
    .then(data => {
        showToast('标注已保存');
        // 重新获取图片列表，更新标注计数
        fetch('/api/images')
            .then(response => response.json())
            .then(data => {
                window.allImages = data.images;
                updateImageList(data.images);
                updateImageCount(data.images.length);
                updateAnnotationProgress(data.images);
                updateWorkflowGuide();
                
                // 如果设置了保存后自动跳转，切换到下一张
                if (shortcutSettings.autoNextAfterSave) {
                    goToNextImage();
                } else {
                    // 保持当前选中的图片不变，只更新UI选中状态，不重新加载标注
                    document.querySelectorAll('.image-item').forEach(item => {
                        item.classList.remove('selected');
                        if (item.dataset.image === currentImage) {
                            item.classList.add('selected');
                        }
                    });
                    // 重绘画布以显示当前标注
                    redrawCanvas();
                }
            })
            .catch(error => {
                console.error('更新图片列表失败:', error);
            });
    })
    .catch(error => {
        console.error('保存标注失败:', error);
        showToast('保存标注失败: ' + error.message, 'error');
    });
}

// 全选图片
function deleteSelectedAnnotation() {
    if (selectedAnnotationId === null) {
        showToast('请先选中一个标注框');
        return;
    }
    
    const index = currentAnnotations.findIndex(a => a.id === selectedAnnotationId);
    if (index !== -1) {
        currentAnnotations.splice(index, 1);
        selectedAnnotationId = null;
        updateAnnotationListDebounced();
        saveAnnotationsSilent();
        redrawCanvas();
        showToast('已删除选中的标注');
    }
}

// 切换到上一张图片
