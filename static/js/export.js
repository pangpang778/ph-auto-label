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
        const safeColor = /^#[0-9a-fA-F]{6}$/.test(cls.color) ? cls.color : '#ff0000';
        const safeName = escapeHtml(cls.name);
        label.innerHTML = `
            <input type="checkbox" name="exportClasses" value="${safeName}" checked>
            <span class="class-color-inline" style="background-color: ${safeColor};"></span>
            ${safeName}
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

// 显示Toast提示
