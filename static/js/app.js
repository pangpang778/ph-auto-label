function initializeApp() {
    updateAnnotationListDebounced = debounce(updateAnnotationList, 100);
    loadShortcutSettings();
    loadClasses();
    loadImages();
    setupEventListeners();
}

// 设置事件监听器
function setupEventListeners() {
    // 导航按钮
    document.getElementById('openFolderBtn').addEventListener('click', showDatasetModal);
    document.getElementById('exportBtn').addEventListener('click', showExportModal);
    document.getElementById('settingsBtn').addEventListener('click', showSettingsModal);
    const timelineBtn = document.getElementById('timelineBtn');
    if (timelineBtn) timelineBtn.addEventListener('click', showTimelineModal);
    const trainCenterBtn = document.getElementById('trainCenterBtn');
    if (trainCenterBtn) trainCenterBtn.addEventListener('click', showTrainingCenterModal);
    const quickImportBtn = document.getElementById('quickImportBtn');
    if (quickImportBtn) quickImportBtn.addEventListener('click', showDatasetModal);
    const quickAiBtn = document.getElementById('quickAiBtn');
    if (quickAiBtn) quickAiBtn.addEventListener('click', () => showAiAnnotateModal(getRecommendedAiEngineForCurrentStep()));
    const quickTrainBtn = document.getElementById('quickTrainBtn');
    if (quickTrainBtn) quickTrainBtn.addEventListener('click', showTrainingCenterModal);
    const quickExportBtn = document.getElementById('quickExportBtn');
    if (quickExportBtn) quickExportBtn.addEventListener('click', showExportModal);
    const workflowNextBtn = document.getElementById('workflowNextBtn');
    if (workflowNextBtn) workflowNextBtn.addEventListener('click', runWorkflowNextAction);
    const workflowPrimaryActionBtn = document.getElementById('workflowPrimaryActionBtn');
    if (workflowPrimaryActionBtn) workflowPrimaryActionBtn.addEventListener('click', runWorkflowNextAction);
    setupWorkflowStepClickEvents();
    document.getElementById('clearAnnotationBtn').addEventListener('click', clearCurrentAnnotations);
    document.getElementById('saveAnnotationBtn').addEventListener('click', saveAnnotations);
    
    // AI标注按钮
    document.getElementById('aiAnnotateToggle').addEventListener('click', toggleAiAnnotate);
    
    // 搜索框
    document.getElementById('imageSearch').addEventListener('input', filterImages);
    
    // 工具按钮
    document.getElementById('rectTool').addEventListener('click', () => switchTool('rect'));
    document.getElementById('polygonTool').addEventListener('click', () => switchTool('polygon'));
    document.getElementById('moveTool').addEventListener('click', () => switchTool('move'));
    
    // 类别管理
    document.getElementById('addClassBtn').addEventListener('click', addClass);
    document.getElementById('newClassInput').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') addClass();
    });
    
    // 画布事件
    const canvas = document.getElementById('imageCanvas');
    canvas.addEventListener('mousedown', handleMouseDown);
    canvas.addEventListener('mousemove', handleMouseMove);
    canvas.addEventListener('mouseup', handleMouseUp);
    canvas.addEventListener('mouseleave', handleMouseLeave);
    canvas.addEventListener('dblclick', handleDoubleClick);
    
    // 模态框关闭事件
    setupModalCloseEvents();
    
    // 数据集上传事件
    setupDatasetUploadEvents();
    
    // 导出表单事件
    document.getElementById('exportForm').addEventListener('submit', handleExport);
    
    // 设置表单事件
    document.getElementById('settingsForm').addEventListener('submit', handleSettingsSave);
    
    // 编辑类别表单事件
    document.getElementById('editClassForm').addEventListener('submit', handleEditClass);
    
    // YOLO11模型管理按钮事件
    document.getElementById('downloadModelsBtn').addEventListener('click', downloadModels);
    document.getElementById('refreshModelsBtn').addEventListener('click', refreshModels);
    
    // YOLO11模型拖放事件
    setupModelDropZoneEvents();
    
    // AI标注模态框事件
    setupAiAnnotateEvents();
    setupTimelineEvents();
    setupTrainingCenterEvents();
    if (typeof setupDepthTrainingEvents === 'function') setupDepthTrainingEvents();
    
    // 快捷键
    document.addEventListener('keydown', handleKeyDown);
    
    // 全选和删除按钮
    document.getElementById('selectAllBtn').addEventListener('click', selectAllImages);
    document.getElementById('deleteSelectedBtn').addEventListener('click', deleteSelectedImages);
}



// 切换工具
function handleKeyDown(e) {
    // 如果正在输入框中，只处理Ctrl+S（防止浏览器默认保存），其他快捷键不处理
    const isInInput = e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT';
    
    // 解析保存快捷键设置
    const saveKey = shortcutSettings.save || 'Ctrl+S';
    const saveKeyParts = saveKey.split('+');
    const saveRequiresCtrl = saveKeyParts.some(p => p.toLowerCase() === 'ctrl');
    const saveRequiresShift = saveKeyParts.some(p => p.toLowerCase() === 'shift');
    const saveRequiresAlt = saveKeyParts.some(p => p.toLowerCase() === 'alt');
    const saveMainKey = saveKeyParts.filter(p => !['ctrl', 'shift', 'alt'].includes(p.toLowerCase())).pop() || 's';
    
    // 检查是否匹配保存快捷键
    const isSaveShortcut = (
        e.key.toUpperCase() === saveMainKey.toUpperCase() &&
        e.ctrlKey === saveRequiresCtrl &&
        e.shiftKey === saveRequiresShift &&
        e.altKey === saveRequiresAlt
    );
    
    // 始终阻止浏览器默认的Ctrl+S行为
    if (e.ctrlKey && e.key.toLowerCase() === 's') {
        e.preventDefault();
    }
    
    // 保存快捷键处理
    if (isSaveShortcut) {
        e.preventDefault();
        if (!isInInput) {
            saveAnnotations();
        }
        return;
    }
    
    // 如果在输入框中，不处理其他快捷键
    if (isInInput) {
        return;
    }
    
    // 删除选中框快捷键
    if (e.key.toUpperCase() === shortcutSettings.deleteSelected.toUpperCase() && !e.ctrlKey && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        deleteSelectedAnnotation();
        return;
    }
    
    // 上一张图片快捷键
    if (e.key.toUpperCase() === shortcutSettings.prevImage.toUpperCase() && !e.ctrlKey && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        goToPrevImage();
        return;
    }
    
    // 下一张图片快捷键
    if (e.key.toUpperCase() === shortcutSettings.nextImage.toUpperCase() && !e.ctrlKey && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        goToNextImage();
        return;
    }
    
    // 数字键1-9快速切换标签类别
    if (e.key >= '1' && e.key <= '9' && !e.ctrlKey && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        const index = parseInt(e.key) - 1;
        selectClassByIndex(index);
        return;
    }
}

// 通过索引选择标签类别
