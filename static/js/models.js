function checkYolo11InstallStatus() {
    // 发送请求检查YOLO11安装状态
    fetch('/api/check-yolo11-install')
        .then(response => response.json())
        .then(data => {
            const isInstalled = data.is_installed;
            const modelsSection = document.querySelector('.yolo11-models-section');
            const downloadModelsBtn = document.getElementById('downloadModelsBtn');
            const refreshModelsBtn = document.getElementById('refreshModelsBtn');
            const modelDropZone = document.getElementById('modelDropZone');
            const modelsContainer = document.getElementById('modelsContainer');
            
            // 更新安装信息显示
            const installInfoElement = document.getElementById('yolo11InstallInfo');
            if (!installInfoElement) {
                // ponytail: install info container absent (e.g. modal not open) - nothing to render
                return;
            }
            if (isInstalled) {
                // 显示详细安装信息
                const installTime = data.install_time || '未知';
                const hardware = data.has_cuda ? 'CUDA (GPU)' : 'CPU';
                installInfoElement.innerHTML = `
                    <p style="margin: 5px 0;"><strong>安装时间:</strong> ${escapeHtml(installTime)}</p>
                    <p style="margin: 5px 0;"><strong>硬件支持:</strong> ${escapeHtml(hardware)}</p>
                `;
                installInfoElement.style.display = 'block';
            } else {
                // 隐藏安装信息
                installInfoElement.innerHTML = '';
                installInfoElement.style.display = 'none';
            }

            // 更新按钮/区域状态（各元素均可能缺失，逐个 guard）
            if (modelsSection) {
                modelsSection.style.opacity = isInstalled ? '1' : '0.5';
                modelsSection.style.pointerEvents = isInstalled ? 'auto' : 'none';
            }
            if (downloadModelsBtn) downloadModelsBtn.disabled = !isInstalled;
            if (refreshModelsBtn) refreshModelsBtn.disabled = !isInstalled;
        })
        .catch(error => {
            console.error('检查YOLO11安装状态失败:', error);
        });
}

// 下载YOLO11预训练模型
function downloadModels() {
    // 获取选中的模型
    const selectedModels = Array.from(document.querySelectorAll('input[name="yolo11Models"]:checked'))
        .map(cb => cb.value);
    
    if (selectedModels.length === 0) {
        showToast('请至少选择一个模型');
        return;
    }
    
    // 获取安装路径
    const installPath = document.getElementById('yolo11InstallPath').value;
    
    // 显示状态
    const statusElement = document.getElementById('modelDownloadStatus');
    const statusText = document.getElementById('modelStatusText');
    statusElement.style.display = 'block';
    statusText.textContent = `正在下载模型: ${selectedModels.join(', ')}...`;
    
    // 禁用下载按钮
    const downloadBtn = document.getElementById('downloadModelsBtn');
    const refreshBtn = document.getElementById('refreshModelsBtn');
    downloadBtn.disabled = true;
    refreshBtn.disabled = true;
    
    // 使用EventSource实现服务器推送进度
    const eventSource = new EventSource(`/api/download-models?models=${encodeURIComponent(selectedModels.join(','))}&install_path=${encodeURIComponent(installPath)}`);
    
    eventSource.onmessage = function(event) {
        try {
            const data = JSON.parse(event.data);
            
            // 更新状态文本
            statusText.textContent = data.message;
            
            // 检查是否下载完成
            if (data.status === 'completed') {
                eventSource.close();
                statusText.textContent = `模型下载完成: ${selectedModels.join(', ')}`;
                // 刷新模型列表
                refreshModels();
                // 恢复按钮状态
                downloadBtn.disabled = false;
                refreshBtn.disabled = false;
                // 5秒后隐藏状态
                setTimeout(() => {
                    statusElement.style.display = 'none';
                }, 5000);
            }
            
            // 检查是否下载失败
            if (data.status === 'error') {
                eventSource.close();
                statusText.textContent = `下载失败: ${data.error}`;
                // 恢复按钮状态
                downloadBtn.disabled = false;
                refreshBtn.disabled = false;
                // 5秒后隐藏状态
                setTimeout(() => {
                    statusElement.style.display = 'none';
                }, 5000);
            }
        } catch (error) {
            console.error('解析下载进度失败:', error);
        }
    };
    
    eventSource.onerror = function() {
        eventSource.close();
        statusText.textContent = '下载过程中发生错误';
        // 恢复按钮状态
        downloadBtn.disabled = false;
        refreshBtn.disabled = false;
        // 5秒后隐藏状态
        setTimeout(() => {
            statusElement.style.display = 'none';
        }, 5000);
    };
}

// 刷新模型列表
function refreshModels() {
    // 获取安装路径
    const installPath = document.getElementById('yolo11InstallPath').value;
    
    // 显示加载状态
    const modelsList = document.getElementById('modelsList');
    modelsList.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 正在加载模型列表...';
    
    // 发送请求获取模型列表
    fetch(`/api/list-models?install_path=${encodeURIComponent(installPath)}`)
        .then(response => response.json())
        .then(data => {
            // 更新模型列表
            if (data.models && data.models.length > 0) {
                modelsList.innerHTML = '';
                data.models.forEach(model => {
                    const modelItem = document.createElement('div');
                    modelItem.className = 'model-item';
                    // ponytail: model name is filename/backend-derived -> escape display, wire delete via data attr (no inline onclick -> no XSS)
                    modelItem.innerHTML = `
                        <i class="fas fa-file-code"></i>
                        <span class="model-name">${escapeHtml(model)}</span>
                        <button class="delete-model-btn" data-model="${escapeHtml(model)}">
                            <i class="fas fa-times"></i>
                        </button>
                    `;
                    const deleteBtn = modelItem.querySelector('.delete-model-btn');
                    deleteBtn.addEventListener('click', () => deleteModel(deleteBtn.dataset.model));
                    modelsList.appendChild(modelItem);
                });
            } else {
                modelsList.innerHTML = '<i class="fas fa-info-circle"></i> 暂无已安装的模型';
            }
        })
        .catch(error => {
            console.error('获取模型列表失败:', error);
            modelsList.innerHTML = '<i class="fas fa-exclamation-triangle"></i> 获取模型列表失败';
        });
}

// 删除模型
function deleteModel(modelName) {
    // 确认删除
    if (!confirm(`确定要删除模型 ${modelName} 吗？`)) {
        return;
    }
    
    // 获取安装路径
    const installPath = document.getElementById('yolo11InstallPath').value;
    
    // 显示状态
    const statusElement = document.getElementById('modelDownloadStatus');
    const statusText = document.getElementById('modelStatusText');
    statusElement.style.display = 'block';
    statusText.textContent = `正在删除模型: ${modelName}...`;
    
    // 发送删除请求
    fetch('/api/delete-model', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-Install-Path': installPath
        },
        body: JSON.stringify({model_name: modelName})
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            statusText.textContent = `模型删除成功: ${modelName}`;
            // 刷新模型列表
            refreshModels();
        } else {
            statusText.textContent = `删除失败: ${data.error}`;
        }
        // 5秒后隐藏状态
        setTimeout(() => {
            statusElement.style.display = 'none';
        }, 5000);
    })
    .catch(error => {
        console.error('删除模型失败:', error);
        statusText.textContent = `删除失败: ${error.message}`;
        // 5秒后隐藏状态
        setTimeout(() => {
            statusElement.style.display = 'none';
        }, 5000);
    });
}

// 设置模型拖放区域事件
function setupModelDropZoneEvents() {
    const dropZone = document.getElementById('modelDropZone');
    
    // 阻止默认拖放行为
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, preventDefaults, false);
    });
    
    // 高亮拖放区域
    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, highlight, false);
    });
    
    // 取消高亮拖放区域
    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, unhighlight, false);
    });
    
    // 处理文件拖放
    dropZone.addEventListener('drop', handleDrop, false);
}

// 阻止默认拖放行为
function handleDrop(e) {
    const files = e.dataTransfer.files;
    if (files.length === 0) return;
    
    // 显示状态
    const statusElement = document.getElementById('modelDownloadStatus');
    const statusText = document.getElementById('modelStatusText');
    statusElement.style.display = 'block';
    statusText.textContent = `正在上传模型文件...`;
    
    // 获取安装路径
    const installPath = document.getElementById('yolo11InstallPath').value;
    
    // 创建FormData对象
    const formData = new FormData();
    Array.from(files).forEach(file => {
        formData.append('files[]', file, file.name);
    });
    
    // 发送文件上传请求
    fetch('/api/upload-model', {
        method: 'POST',
        body: formData,
        headers: {
            'X-Install-Path': installPath
        }
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            statusText.textContent = `模型文件上传成功: ${data.uploaded_files.join(', ')}`;
            // 刷新模型列表
            refreshModels();
        } else {
            statusText.textContent = `上传失败: ${data.error}`;
        }
    })
    .catch(error => {
        console.error('上传模型文件失败:', error);
        statusText.textContent = `上传失败: ${error.message}`;
    })
    .finally(() => {
        // 5秒后隐藏状态
        setTimeout(() => {
            statusElement.style.display = 'none';
        }, 5000);
    });
}

// 显示设置模态框
function showSettingsModal() {
    document.getElementById('settingsModal').style.display = 'block';
    // 检查YOLO11安装状态并更新UI
    checkYolo11InstallStatus();
    // 刷新模型列表
    refreshModels();
    
    // 加载快捷键设置到表单
    document.getElementById('deleteSelectedShortcut').value = shortcutSettings.deleteSelected || 'Q';
    document.getElementById('saveShortcut').value = shortcutSettings.save || 'Ctrl+S';
    document.getElementById('prevImageShortcut').value = shortcutSettings.prevImage || 'A';
    document.getElementById('nextImageShortcut').value = shortcutSettings.nextImage || 'D';
    document.getElementById('autoNextAfterSave').checked = shortcutSettings.autoNextAfterSave || false;
}

function openSettingsToModelInstall() {
    showSettingsModal();

    const modal = document.getElementById('settingsModal');
    if (!modal) return;

    const accordionItems = modal.querySelectorAll('.accordion-item');
    let targetItem = null;
    accordionItems.forEach((item) => {
        const headerText = item.querySelector('.accordion-header span')?.textContent || '';
        if (headerText.includes('YOLO11 模型管理')) {
            targetItem = item;
        }
    });

    if (!targetItem) return;

    // 聚焦到模型管理区域，避免用户手动翻找
    accordionItems.forEach((item) => {
        const body = item.querySelector('.accordion-body');
        if (item === targetItem) {
            item.classList.add('active');
            if (body) body.style.display = 'block';
        } else {
            item.classList.remove('active');
            if (body) body.style.display = 'none';
        }
    });

    targetItem.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// 处理导出表单提交
function handleSettingsSave(e) {
    e.preventDefault();
    
    // 读取快捷键设置
    shortcutSettings.deleteSelected = document.getElementById('deleteSelectedShortcut').value || 'Q';
    shortcutSettings.save = document.getElementById('saveShortcut').value || 'Ctrl+S';
    shortcutSettings.prevImage = document.getElementById('prevImageShortcut').value || 'A';
    shortcutSettings.nextImage = document.getElementById('nextImageShortcut').value || 'D';
    shortcutSettings.autoNextAfterSave = document.getElementById('autoNextAfterSave').checked;
    
    // 保存到localStorage
    localStorage.setItem('xiabie_shortcuts', JSON.stringify(shortcutSettings));
    
    showToast('设置已保存');
    document.getElementById('settingsModal').style.display = 'none';
}

// 处理键盘快捷键

// ============================================================
// Model Evaluation - per-row 评估 button in the model registry
// (training_ui.js renders the registry table; models.js augments
// each row with an evaluation launcher + inline result area).
// Conforms to the evaluation API + schema contract.
// ============================================================
(function () {
    'use strict';

    var POLL_INTERVAL = 1500;
    // recordId -> setTimeout handle, so we can stop polling per-row
    var pollers = {};

    function escapeHtmlEval(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (ch) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[ch];
        });
    }
    function fmtNumEval(v, digits) {
        var n = Number(v);
        if (!isFinite(n)) return '-';
        return n.toFixed(digits == null ? 3 : digits);
    }

    // Extract the model id from a registry row by reading the activate button's
    // onclick attribute (rendered by training_ui.js as activateModel('<id>')).
    function modelIdFromRow(row) {
        var btn = row.querySelector('button[onclick*="activateModel"]');
        if (!btn) return null;
        var m = (btn.getAttribute('onclick') || '').match(/activateModel\(\s*['"]([^'"]+)['"]/);
        return m ? m[1] : null;
    }

    function ensureEvalCell(row) {
        var actions = row.querySelector('.artifact-actions');
        if (!actions) return null;
        if (actions.querySelector('.eval-launch-btn')) return actions;
        var modelId = modelIdFromRow(row);
        if (!modelId) return null;

        var btn = document.createElement('button');
        btn.className = 'btn btn-small btn-info eval-launch-btn';
        btn.type = 'button';
        btn.innerHTML = '<i class="fas fa-chart-line"></i> 评估';
        btn.addEventListener('click', function () { startRowEvaluation(modelId, row); });
        actions.appendChild(btn);

        // inline result/progress area beneath the actions cell
        var result = document.createElement('div');
        result.className = 'eval-row-result';
        result.style.cssText = 'margin-top:6px;font-size:12px;color:#6e6e73;';
        // place after the actions div inside the last <td>
        var td = actions.closest('td');
        if (td) td.appendChild(result);
        return actions;
    }

    function setRowResult(row, html, level) {
        var td = row.querySelector('.artifact-actions') && row.querySelector('.artifact-actions').closest('td');
        if (!td) return;
        var result = td.querySelector('.eval-row-result');
        if (!result) {
            result = document.createElement('div');
            result.className = 'eval-row-result';
            result.style.cssText = 'margin-top:6px;font-size:12px;';
            td.appendChild(result);
        }
        result.innerHTML = html;
        var color = level === 'error' ? '#ff3b30' : (level === 'success' ? '#1a7c3e' : '#6e6e73');
        result.style.color = color;
    }

    function setRowProgress(row, percent) {
        setRowResult(row,
            '<div style="display:flex;align-items:center;gap:8px;">' +
                '<span>评估中... ' + Math.max(0, Math.min(100, Math.round(percent))) + '%</span>' +
                '<div style="flex:1;max-width:120px;height:6px;border-radius:999px;background:rgba(0,0,0,0.08);overflow:hidden;">' +
                    '<div style="height:100%;width:' + Math.max(0, Math.min(100, percent)) + '%;background:#007aff;border-radius:999px;"></div>' +
                '</div>' +
            '</div>', null);
    }

    function startRowEvaluation(modelId, row) {
        fetch('/api/models/' + encodeURIComponent(modelId) + '/evaluate', { method: 'POST' })
            .then(function (resp) {
                return resp.json().then(function (data) {
                    return { ok: resp.ok, status: resp.status, data: data };
                });
            })
            .then(function (r) {
                if (r.status === 409) {
                    var msg = (r.data && r.data.error) || '训练中，暂无法评估';
                    setRowResult(row, escapeHtmlEval(msg), 'error');
                    if (typeof showToast === 'function') showToast(msg);
                    return;
                }
                if (!r.ok || !r.data || !r.data.id) {
                    var err = (r.data && r.data.error) || ('启动评估失败 (' + r.status + ')');
                    setRowResult(row, escapeHtmlEval(err), 'error');
                    if (typeof showToast === 'function') showToast(err);
                    return;
                }
                setRowProgress(row, 0);
                pollRowEval(r.data.id, row);
            })
            .catch(function (e) {
                setRowResult(row, '网络错误: ' + escapeHtmlEval(e && e.message), 'error');
            });
    }

    function pollRowEval(recordId, row) {
        if (pollers[recordId]) { clearTimeout(pollers[recordId]); }
        var tick = function () {
            fetch('/api/evaluations/' + encodeURIComponent(recordId))
                .then(function (resp) { return resp.json(); })
                .then(function (rec) {
                    if (!rec) { pollers[recordId] = setTimeout(tick, POLL_INTERVAL); return; }
                    var progress = Math.max(0, Math.min(100, Number(rec.progress || 0)));
                    if (rec.status === 'completed' || rec.status === 'failed') {
                        delete pollers[recordId];
                        if (rec.status === 'failed') {
                            setRowResult(row, '评估失败：' + escapeHtmlEval(rec.error || '未知错误'), 'error');
                        } else {
                            var m = rec.val || {};
                            setRowResult(row,
                                '<div>mAP@0.5: <strong>' + escapeHtmlEval(fmtNumEval(m.map50)) + '</strong> · ' +
                                'mAP@0.5:0.95: <strong>' + escapeHtmlEval(fmtNumEval(m.map50_95)) + '</strong></div>' +
                                '<a href="/evaluation" style="color:#007aff;text-decoration:none;">查看对比 -&gt;</a>',
                                'success');
                        }
                        return;
                    }
                    setRowProgress(row, progress);
                    pollers[recordId] = setTimeout(tick, POLL_INTERVAL);
                })
                .catch(function () { pollers[recordId] = setTimeout(tick, POLL_INTERVAL * 2); });
        };
        pollers[recordId] = setTimeout(tick, POLL_INTERVAL);
    }

    // Augment registry rows whenever the panel is (re)rendered.
    function augmentRegistry() {
        var panel = document.getElementById('modelRegistryPanel');
        if (!panel) return;
        var rows = panel.querySelectorAll('table tbody tr');
        rows.forEach(function (row) { ensureEvalCell(row); });
    }

    // The registry is rendered by training_ui.js on demand (modal open / refresh).
    // Observe the panel so 评估 buttons are injected whenever rows appear.
    function setupRegistryObserver() {
        var panel = document.getElementById('modelRegistryPanel');
        if (!panel) return;
        augmentRegistry();
        if (typeof MutationObserver === 'undefined') return;
        var observer = new MutationObserver(function () { augmentRegistry(); });
        observer.observe(panel, { childList: true, subtree: true });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', setupRegistryObserver);
    } else {
        setupRegistryObserver();
    }
})();
