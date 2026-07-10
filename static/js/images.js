function loadImages() {
    fetch('/api/images')
        .then(response => response.json())
        .then(data => {
            window.allImages = data.images;
            updateImageList(data.images);
            updateImageCount(data.images.length);
            updateAnnotationProgress(data.images);
            updateWorkflowGuide();
            
            // 检查URL参数，看是否需要直接打开某个图片
            const urlParams = new URLSearchParams(window.location.search);
            const imageName = urlParams.get('image');
            
            if (imageName) {
                // 如果URL参数指定了图片，检查该图片是否存在
                const imageExists = data.images.some(img => img.name === imageName);
                if (imageExists) {
                    selectImage(imageName);
                    return;
                }
            }
            
            // 如果URL参数无效或未指定，默认选中第一张图片（如果有）
            if (data.images.length > 0) {
                selectImage(data.images[0].name);
            } else {
                // 如果没有图片，显示无图片提示
                document.getElementById('noImageMessage').style.display = 'block';
                document.getElementById('imageCanvasContainer').style.display = 'none';
                currentImage = null;
            }
        })
        .catch(error => {
            console.error('加载图片列表失败:', error);
            showToast('加载图片列表失败');
        });
}

// 更新图片列表
function updateImageList(images) {
    const imageList = document.getElementById('imageList');
    imageList.innerHTML = '';
    
    images.forEach((image, index) => {
        const li = document.createElement('li');
        li.className = 'image-item';
        li.dataset.image = image.name;

        // 检查是否有标注
        const hasAnnotations = image.annotation_count > 0;

        const safeName = escapeHtml(image.name);
        li.innerHTML = `
            <div class="image-checkbox">
                <input type="checkbox" class="image-checkbox-input">
            </div>
            <div class="annotation-status">
                ${hasAnnotations ?
                  '<i class="fas fa-check-circle annotated" title="已标注"></i>' :
                  '<i class="far fa-circle unannotated" title="未标注"></i>'}
            </div>
            <div class="image-index">${index + 1}</div>
            <div class="image-name" title="${safeName}">${safeName}</div>
        `;
        imageList.appendChild(li);
    });
    
    // 添加点击事件
    document.querySelectorAll('.image-item').forEach(item => {
        item.addEventListener('click', function(e) {
            if (e.target.type !== 'checkbox') {
                const imageName = this.dataset.image;
                selectImage(imageName);
            }
        });
    });
    
    // 添加复选框事件
    document.querySelectorAll('.image-checkbox-input').forEach(checkbox => {
        checkbox.addEventListener('change', updateDeleteButtonState);
    });
    
    // 不再需要删除按钮事件监听器
}

// 更新图片计数
function updateImageCount(count) {
    document.getElementById('imageCount').textContent = `共 ${count} 张图片`;
}

// 更新标注进度
function updateAnnotationProgress(images) {
    const total = images ? images.length : (window.allImages ? window.allImages.length : 0);
    const annotated = images ? images.filter(img => img.annotation_count > 0).length : 
                      (window.allImages ? window.allImages.filter(img => img.annotation_count > 0).length : 0);
    
    document.getElementById('annotatedCount').textContent = annotated;
    document.getElementById('totalImageCount').textContent = total;
}

function filterImages() {
    const searchTerm = document.getElementById('imageSearch').value.toLowerCase();
    const filteredImages = window.allImages.filter(image => 
        image.name.toLowerCase().includes(searchTerm)
    );
    updateImageList(filteredImages);
}

// 选择图片
function selectImage(imageName, skipLoadAnnotations = false) {
    // 更新UI选中状态
    document.querySelectorAll('.image-item').forEach(item => {
        item.classList.remove('selected');
        if (item.dataset.image === imageName) {
            item.classList.add('selected');
        }
    });
    
    currentImage = imageName;
    
    // 隐藏无图片提示
    document.getElementById('noImageMessage').style.display = 'none';
    
    // 显示画布容器
    document.getElementById('imageCanvasContainer').style.display = 'block';
    
    // 加载标注，除非跳过
    if (!skipLoadAnnotations) {
        loadAnnotations(imageName);
    }
    
    // 如果AI标注已开启，自动进行AI标注
    if (aiAnnotateEnabled && !skipLoadAnnotations) {
        performAiAnnotate();
    }
}

// 加载标注
function loadAnnotations(imageName) {
    fetch(`/api/annotations/${imageName}`)
        .then(response => response.json())
        .then(data => {
            currentAnnotations = data || [];
            updateAnnotationListDebounced();
            redrawCanvas();
        })
        .catch(error => {
            console.error('加载标注失败:', error);
            currentAnnotations = [];
            updateAnnotationListDebounced();
            redrawCanvas();
        });
}

// 更新标注列表
function selectAllImages() {
    const checkboxes = document.querySelectorAll('.image-checkbox-input');
    const allSelected = Array.from(checkboxes).every(cb => cb.checked);
    
    checkboxes.forEach(cb => {
        cb.checked = !allSelected;
    });
    
    updateDeleteButtonState();
}

// 更新删除按钮状态
function updateDeleteButtonState() {
    const checkedCount = document.querySelectorAll('.image-checkbox-input:checked').length;
    const deleteBtn = document.getElementById('deleteSelectedBtn');
    
    if (checkedCount > 0) {
        deleteBtn.disabled = false;
        deleteBtn.title = `删除选中的 ${checkedCount} 张图片`;
    } else {
        deleteBtn.disabled = true;
        deleteBtn.title = '删除选中';
    }
}

// 删除选中图片
function deleteSelectedImages() {
    const checkedItems = document.querySelectorAll('.image-checkbox-input:checked');
    
    if (checkedItems.length === 0) {
        showToast('请先选择要删除的图片');
        return;
    }
    
    if (!confirm(`确定要删除选中的 ${checkedItems.length} 张图片吗？`)) {
        return;
    }
    
    const imageNames = Array.from(checkedItems).map(cb => {
        return cb.closest('.image-item').dataset.image;
    });
    
    fetch('/api/images/delete', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({images: imageNames})
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            showToast(`成功删除 ${imageNames.length} 张图片`);
            // 重新加载图片列表
            loadImages();
            // 清除选中状态
            checkedItems.forEach(cb => cb.checked = false);
            updateDeleteButtonState();
        } else {
            throw new Error(data.error || '删除失败');
        }
    })
    .catch(error => {
        console.error('删除图片失败:', error);
        showToast('删除图片失败: ' + error.message);
    });
}

// 显示数据集模态框
function goToPrevImage() {
    if (!window.allImages || window.allImages.length === 0) return;
    
    const currentIndex = window.allImages.findIndex(img => img.name === currentImage);
    if (currentIndex === -1) return;
    
    const prevIndex = currentIndex - 1;
    if (prevIndex >= 0) {
        selectImage(window.allImages[prevIndex].name);
    } else {
        showToast('已经是第一张图片');
    }
}

// 设置模态框关闭事件
function goToNextImage() {
    if (!window.allImages || window.allImages.length === 0) return;
    
    const currentIndex = window.allImages.findIndex(img => img.name === currentImage);
    if (currentIndex === -1) return;
    
    const nextIndex = currentIndex + 1;
    if (nextIndex < window.allImages.length) {
        selectImage(window.allImages[nextIndex].name);
    } else {
        showToast('已经是最后一张图片');
    }
}


// ==================== 批量AI标注功能 ====================

// 批量标注状态
let batchAnnotateRunning = false;
let batchAnnotateCancelled = false;

// 更新批量标注范围信息
