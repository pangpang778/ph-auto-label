function showDatasetModal() {
    document.getElementById('datasetModal').style.display = 'block';
    updateWorkflowGuide();
}

// 显示导出模态框
function showExportModal() {
    // 加载类别到导出表单
    const container = document.getElementById('classCheckboxes');
    container.innerHTML = '';
    
    classes.forEach(cls => {
        const label = document.createElement('label');
        label.className = 'class-checkbox-label';
        label.innerHTML = `
            <input type="checkbox" name="exportClasses" value="${cls.name}" checked>
            <span class="class-color-inline" style="background-color: ${cls.color};"></span>
            ${cls.name}
        `;
        container.appendChild(label);
    });
    
    // 设置默认比例
    document.getElementById('trainRatio').value = 0.7;
    document.getElementById('valRatio').value = 0.2;
    document.getElementById('testRatio').value = 0.1;
    
    document.getElementById('exportModal').style.display = 'block';
}

// 检查YOLO11安装状态并更新UI
function handleExport(e) {
    e.preventDefault();
    
    // 获取表单数据
    const formData = new FormData(e.target);
    const trainRatio = parseFloat(formData.get('trainRatio'));
    const valRatio = parseFloat(formData.get('valRatio'));
    const testRatio = parseFloat(formData.get('testRatio'));

    // 获取选中的类别
    const selectedClasses = Array.from(document.querySelectorAll('input[name="exportClasses"]:checked'))
        .map(cb => cb.value);
    
    if (selectedClasses.length === 0) {
        showToast('请至少选择一个类别');
        return;
    }
    
    // 检查比例总和
    // const total = trainRatio + valRatio + testRatio;
    // if (Math.abs(total - 1.0) > 0.001) {
    //     showToast('训练集、验证集和测试集比例之和必须等于1');
    //     return;
    // }
    
    // 获取样本选择选项和文件前缀
    const sampleSelection = formData.get('sampleSelection');
    const exportDataType = formData.get('exportDataType');
    const exportPrefix = document.getElementById('exportPrefix').value;
    
    // 显示加载指示器
    document.getElementById('exportSubmitBtn').style.display = 'none';
    document.getElementById('exportLoadingIndicator').style.display = 'block';
    
    // 发送导出请求
    fetch('/api/export', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            train_ratio: trainRatio,
            val_ratio: valRatio,
            test_ratio: testRatio,
            selected_classes: selectedClasses,
            sample_selection: sampleSelection,
            export_data_type: exportDataType,
            export_prefix: exportPrefix
        })
    })
    .then(response => {
        if (response.ok) {
            return response.blob().then(blob => {
                // 生成带时间戳的文件名，格式：datasets_年月日时分秒.zip
                const now = new Date();
                const year = now.getFullYear();
                const month = String(now.getMonth() + 1).padStart(2, '0');
                const day = String(now.getDate()).padStart(2, '0');
                const hours = String(now.getHours()).padStart(2, '0');
                const minutes = String(now.getMinutes()).padStart(2, '0');
                const seconds = String(now.getSeconds()).padStart(2, '0');
                const filename = `datasets_${year}${month}${day}${hours}${minutes}${seconds}.zip`;
                
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);
                
                // 隐藏模态框
                document.getElementById('exportModal').style.display = 'none';
            });
        } else {
            return response.json().then(data => {
                throw new Error(data.error || '导出失败');
            });
        }
    })
    .catch(error => {
        console.error('导出失败:', error);
        showToast('导出失败: ' + error.message);
    })
    .finally(() => {
        // 隐藏加载指示器
        document.getElementById('exportSubmitBtn').style.display = 'block';
        document.getElementById('exportLoadingIndicator').style.display = 'none';
    });
}

// 处理设置保存
function setupModalCloseEvents() {
    document.querySelectorAll('.modal .close').forEach(closeBtn => {
        closeBtn.addEventListener('click', function() {
            this.closest('.modal').style.display = 'none';
        });
    });
    
    document.querySelectorAll('.modal').forEach(modal => {
        modal.addEventListener('click', function(e) {
            if (e.target === modal) {
                modal.style.display = 'none';
            }
        });
    });
}

// 设置数据集上传事件
function setupDatasetUploadEvents() {
    // 图片文件夹上传
    const selectFolderBtn = document.getElementById('selectFolderBtn');
    const folderInput = document.getElementById('folderInput');
    const uploadImagesBtn = document.getElementById('uploadImagesBtn');
    if (selectFolderBtn && folderInput && uploadImagesBtn) {
        selectFolderBtn.addEventListener('click', function() {
            folderInput.click();
        });
        
        folderInput.addEventListener('change', function(e) {
            // 处理选中的图片文件
            const files = Array.from(e.target.files);
            if (files.length > 0) {
                // 显示选中的文件数量
                const uploadArea = document.getElementById('imageUploadArea');
                const fileCount = document.createElement('div');
                fileCount.className = 'file-count';
                fileCount.textContent = `已选择 ${files.length} 个文件`;
                fileCount.style.marginTop = '10px';
                fileCount.style.fontSize = '0.9em';
                fileCount.style.color = '#666';
                
                // 移除之前的文件数量显示
                const existingCount = uploadArea.querySelector('.file-count');
                if (existingCount) {
                    existingCount.remove();
                }
                
                uploadArea.appendChild(fileCount);
                
                // 启用上传按钮
                uploadImagesBtn.disabled = false;
            }
        });
        
        // 上传图片按钮事件
        uploadImagesBtn.addEventListener('click', function() {
            const files = Array.from(folderInput.files);
            if (files.length === 0) {
                showToast('请先选择图片文件');
                return;
            }
            
            // 显示上传中状态
            uploadImagesBtn.disabled = true;
            uploadImagesBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 上传中...';
            
            // 创建FormData对象，用于发送文件
            const formData = new FormData();
            files.forEach(file => {
                formData.append('files[]', file, file.name);
            });
            
            // 发送真实的文件上传请求
            fetch('/api/upload', {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                // 重置按钮状态
                uploadImagesBtn.innerHTML = '<i class="fas fa-upload"></i> 上传图片到数据集';
                uploadImagesBtn.disabled = false;
                
                // 显示成功提示
                showToast(`成功上传 ${files.length} 张图片`);
                
                // 关闭模态框
                document.getElementById('datasetModal').style.display = 'none';
                
                // 重新加载图片列表
                loadImages();
            })
            .catch(error => {
                console.error('上传失败:', error);
                
                // 重置按钮状态
                uploadImagesBtn.innerHTML = '<i class="fas fa-upload"></i> 上传图片到数据集';
                uploadImagesBtn.disabled = false;
                
                // 显示错误提示
                showToast('上传失败，请重试');
            });
        });
    }
    
    // 视频文件上传
    const selectVideoBtn = document.getElementById('selectVideoBtn');
    const videoInput = document.getElementById('videoInput');
    if (selectVideoBtn && videoInput) {
        selectVideoBtn.addEventListener('click', function() {
            videoInput.click();
        });
        
        videoInput.addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (file) {
                const selectedVideoInfo = document.getElementById('selectedVideoInfo');
                const selectedVideoName = document.getElementById('selectedVideoName');
                selectedVideoName.textContent = file.name;
                selectedVideoInfo.style.display = 'block';
                
                // 启用抽帧按钮
                const extractFramesBtn = document.getElementById('extractFramesBtn');
                if (extractFramesBtn) {
                    extractFramesBtn.disabled = false;
                }
            }
        });
    }
    
    // 视频抽帧按钮
    const extractFramesBtn = document.getElementById('extractFramesBtn');
    const frameIntervalInput = document.getElementById('frameInterval');
    if (extractFramesBtn && videoInput && frameIntervalInput) {
        extractFramesBtn.addEventListener('click', function() {
            const files = videoInput.files;
            if (files.length === 0) {
                showToast('请先选择视频文件');
                return;
            }
            
            // 获取抽帧间隔
            const frameInterval = parseInt(frameIntervalInput.value) || 30;
            
            // 显示上传中状态
            extractFramesBtn.disabled = true;
            extractFramesBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 抽帧中...';
            
            // 创建FormData对象，用于发送视频文件和抽帧间隔
            const formData = new FormData();
            formData.append('video', files[0], files[0].name);
            formData.append('frame_interval', frameInterval);
            
            // 发送真实的视频抽帧请求
            fetch('/api/upload/video', {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                // 重置按钮状态
                extractFramesBtn.innerHTML = '<i class="fas fa-film"></i> 抽帧并添加到数据集';
                extractFramesBtn.disabled = false;
                
                if (data.error) {
                    // 显示错误提示
                    showToast(`抽帧失败: ${data.error}`);
                } else {
                    // 显示成功提示
                    showToast(`成功从视频中提取 ${data.count} 帧图片`);
                    
                    // 关闭模态框
                    document.getElementById('datasetModal').style.display = 'none';
                    
                    // 重新加载图片列表
                    loadImages();
                }
            })
            .catch(error => {
                console.error('抽帧失败:', error);
                
                // 重置按钮状态
                extractFramesBtn.innerHTML = '<i class="fas fa-film"></i> 抽帧并添加到数据集';
                extractFramesBtn.disabled = false;
                
                // 显示错误提示
                showToast('抽帧失败，请重试');
            });
        });
    }
    
    // LabelMe数据集上传
    const selectLabelMeBtn = document.getElementById('selectLabelMeBtn');
    const labelmeInput = document.getElementById('labelmeInput');
    const uploadLabelMeBtn = document.getElementById('uploadLabelMeBtn');
    if (selectLabelMeBtn && labelmeInput && uploadLabelMeBtn) {
        selectLabelMeBtn.addEventListener('click', function() {
            labelmeInput.click();
        });
        
        labelmeInput.addEventListener('change', function(e) {
            // 处理选中的LabelMe文件
            const files = Array.from(e.target.files);
            if (files.length > 0) {
                // 显示选中的文件数量
                const uploadArea = document.getElementById('labelmeUploadArea');
                const fileCount = document.createElement('div');
                fileCount.className = 'file-count';
                fileCount.textContent = `已选择 ${files.length} 个文件`;
                fileCount.style.marginTop = '10px';
                fileCount.style.fontSize = '0.9em';
                fileCount.style.color = '#666';
                
                // 移除之前的文件数量显示
                const existingCount = uploadArea.querySelector('.file-count');
                if (existingCount) {
                    existingCount.remove();
                }
                
                uploadArea.appendChild(fileCount);
                
                // 启用上传按钮
                uploadLabelMeBtn.disabled = false;
            }
        });
        
        // 上传LabelMe数据集按钮事件
        uploadLabelMeBtn.addEventListener('click', function() {
            const files = Array.from(labelmeInput.files);
            if (files.length === 0) {
                showToast('请先选择LabelMe数据集文件');
                return;
            }
            
            // 显示上传中状态
            uploadLabelMeBtn.disabled = true;
            uploadLabelMeBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 上传中...';
            
            // 创建FormData对象，用于发送文件
            const formData = new FormData();
            files.forEach(file => {
                formData.append('files', file, file.name);
            });
            
            // 发送真实的文件上传请求
            fetch('/api/upload-labelme', {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                // 重置按钮状态
                uploadLabelMeBtn.innerHTML = '<i class="fas fa-upload"></i> 上传labelme数据集';
                uploadLabelMeBtn.disabled = false;
                
                // 显示成功提示
                showToast(`成功上传 ${files.length} 个LabelMe文件`);
                
                // 关闭模态框
                document.getElementById('datasetModal').style.display = 'none';
                
                // 重新加载图片列表和类别列表
                loadImages();
                loadClasses();
            })
            .catch(error => {
                console.error('上传失败:', error);
                
                // 重置按钮状态
                uploadLabelMeBtn.innerHTML = '<i class="fas fa-upload"></i> 上传labelme数据集';
                uploadLabelMeBtn.disabled = false;
                
                // 显示错误提示
                showToast('上传失败，请重试');
            });
        });
    }
    
    // 标签页切换事件
    const tabBtns = document.querySelectorAll('.tab-btn');
    tabBtns.forEach(btn => {
        btn.addEventListener('click', function() {
            // 移除所有标签页的active状态
            tabBtns.forEach(b => b.classList.remove('active'));
            
            // 添加当前标签页的active状态
            this.classList.add('active');
            
            // 隐藏所有内容面板
            const tabContents = document.querySelectorAll('.tab-pane');
            tabContents.forEach(content => content.classList.remove('active'));
            
            // 显示对应内容面板
            const tabId = this.getAttribute('data-tab');
            const targetTab = document.getElementById(`${tabId}-tab`);
            if (targetTab) {
                targetTab.classList.add('active');
            }
        });
    });
}

// 显示Toast提示
