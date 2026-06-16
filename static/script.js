
let currentImage = null;
let currentAnnotations = [];
let classes = [];
let isDrawing = false;
let startPoint = null;
let currentPoint = null;
let currentTool = 'rect'; // 默认工具
let imageCache = new Map(); // 图片缓存
let selectedAnnotationId = null; // 当前选中的标注ID
let isResizing = false; // 是否正在调整大小
let isMoving = false; // 是否正在移动标注
let resizeHandle = null; // 当前调整大小的控制点
let lastMousePos = null; // 上次鼠标位置
let polygonPoints = []; // 多边形绘制时的顶点数组
let isPolygonDrawing = false; // 是否正在绘制多边形
let updateAnnotationListDebounced = debounce(updateAnnotationList, 100); // 防抖后的标注列表更新函数

// AI标注相关状态
let aiAnnotateEnabled = false; // AI标注是否开启
let aiAnnotateModel = ''; // 当前选择的AI模型
let aiAnnotateConfidence = 0.5; // AI标注置信度阈值
let aiAutoNext = false; // 保存后是否自动切换下一张（默认关闭）
let aiAnnotating = false; // 是否正在进行AI标注
let aiAnnotateEngine = 'yolo11'; // AI标注引擎（yolo11 | sam3）
let aiAutoRangeStart = null; // AI自动标注起始序号（1-based）
let aiAutoRangeEnd = null; // AI自动标注结束序号（1-based）
let workflowSelectedStep = null; // 用户手动选择的步骤（1-6）
const COLD_START_MIN_ANNOTATED = 20;

// SOP/训练中心状态
let sopScenario = {steps: [], object_classes: [], action_labels: []};
let timelineSegments = [];
let currentTimelineVideo = '';
let trainCenterPolling = null;

// 快捷键设置
let shortcutSettings = {
    deleteSelected: 'Q',
    save: 'Ctrl+S',
    prevImage: 'A',
    nextImage: 'D',
    autoNextAfterSave: false
};

// 从localStorage加载快捷键设置
function loadShortcutSettings() {
    const saved = localStorage.getItem('xiabie_shortcuts');
    if (saved) {
        try {
            shortcutSettings = JSON.parse(saved);
        } catch (e) {
            console.error('加载快捷键设置失败:', e);
        }
    }
}

// 防抖函数
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// DOM加载完成后初始化
document.addEventListener('DOMContentLoaded', function() {
    initializeApp();
});

// 初始化应用
function initializeApp() {
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
    
    // 快捷键
    document.addEventListener('keydown', handleKeyDown);
    
    // 全选和删除按钮
    document.getElementById('selectAllBtn').addEventListener('click', selectAllImages);
    document.getElementById('deleteSelectedBtn').addEventListener('click', deleteSelectedImages);
}



// 切换工具
function switchTool(tool) {
    // 更新UI状态
    document.querySelectorAll('.tool-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    document.getElementById(tool + 'Tool').classList.add('active');
    
    // 重置所有绘制状态
    isDrawing = false;
    isPolygonDrawing = false;
    startPoint = null;
    currentPoint = null;
    polygonPoints = [];
    
    // 设置当前工具
    currentTool = tool;
    
    // 更新鼠标样式
    const canvas = document.getElementById('imageCanvas');
    canvas.style.cursor = 'crosshair';
    
    redrawCanvas();
}

// 处理鼠标按下事件
function handleMouseDown(e) {
    if (!currentImage) return;
    
    const rect = e.target.getBoundingClientRect();
    const canvas = e.target;
    
    // 获取图片的实际尺寸和位置
    const img = imageCache.get(currentImage);
    if (!img) return;
    
    // 计算图片在画布上的显示尺寸和位置（自适应居中）
    const container = document.getElementById('imageCanvasContainer');
    const maxWidth = container.clientWidth - 20;
    const maxHeight = container.clientHeight - 20;
    const ratio = Math.min(maxWidth / img.width, maxHeight / img.height);
    const scaledWidth = img.width * ratio;
    const scaledHeight = img.height * ratio;
    const imgX = (container.clientWidth - scaledWidth) / 2;
    const imgY = (container.clientHeight - scaledHeight) / 2;
    
    // 计算鼠标在画布上的坐标
    const canvasX = e.clientX - rect.left;
    const canvasY = e.clientY - rect.top;
    
    // 计算鼠标在图片上的实际坐标
    const x = (canvasX - imgX) / ratio;
    const y = (canvasY - imgY) / ratio;
    
    // 检查是否点击了某个标注的控制点
    const resizeResult = checkResizeHandleClick(canvasX, canvasY, ratio, imgX, imgY);
    if (resizeResult) {
        isResizing = true;
        resizeHandle = resizeResult.handle;
        selectedAnnotationId = resizeResult.annotationId;
        lastMousePos = {x: e.clientX, y: e.clientY};
        updateAnnotationListDebounced();
        redrawCanvas();
        return;
    }
    
    // 检查是否点击了某个标注
    const annotationResult = checkAnnotationClick(canvasX, canvasY, ratio, imgX, imgY);
    if (annotationResult) {
        selectedAnnotationId = annotationResult.id;
        isMoving = true;
        lastMousePos = {x: e.clientX, y: e.clientY};
        updateAnnotationListDebounced();
        redrawCanvas();
        return;
    }
    
    // 如果点击了空白区域，取消选择
    selectedAnnotationId = null;
    updateAnnotationListDebounced();
    redrawCanvas();
    // 处理多边形绘制
    if (currentTool === 'polygon') {
        // 如果还没有开始绘制多边形，初始化
        if (!isPolygonDrawing) {
            isPolygonDrawing = true;
            polygonPoints = [];
        }
        
        // 添加当前点到多边形顶点数组
        polygonPoints.push({x: x, y: y});
        
        // 更新当前点用于绘制
        currentPoint = {x: x, y: y};
        
        redrawCanvas();
        return;
    }
    
    // 处理矩形绘制
    if (currentTool === 'rect') {
        // 绘制工具 - 开始绘制
        isDrawing = true;
        startPoint = {x: x, y: y};
        currentPoint = {x: x, y: y};
        redrawCanvas();
    }
}

// 检查是否点击了调整大小的控制点
function checkResizeHandleClick(canvasX, canvasY, ratio, imgX, imgY) {
    for (const annotation of currentAnnotations) {
        if (annotation.type !== 'rectangle' || annotation.points.length < 4) continue;
        
        // 计算矩形的四个角点
        const points = annotation.points;
        const x1 = points[0][0] * ratio + imgX;
        const y1 = points[0][1] * ratio + imgY;
        const x2 = points[2][0] * ratio + imgX;
        const y2 = points[2][1] * ratio + imgY;
        
        // 控制点位置
        const handles = [
            { x: x1, y: y1, type: 'nw' },
            { x: (x1 + x2) / 2, y: y1, type: 'n' },
            { x: x2, y: y1, type: 'ne' },
            { x: x2, y: (y1 + y2) / 2, type: 'e' },
            { x: x2, y: y2, type: 'se' },
            { x: (x1 + x2) / 2, y: y2, type: 's' },
            { x: x1, y: y2, type: 'sw' },
            { x: x1, y: (y1 + y2) / 2, type: 'w' }
        ];
        
        // 检查是否点击了某个控制点
        for (const handle of handles) {
            const distance = Math.sqrt(
                Math.pow(canvasX - handle.x, 2) + Math.pow(canvasY - handle.y, 2)
            );
            if (distance <= 8) {
                return { annotationId: annotation.id, handle: handle.type };
            }
        }
    }
    return null;
}

// 检查是否点击了某个标注
function checkAnnotationClick(canvasX, canvasY, ratio, imgX, imgY) {
    for (const annotation of currentAnnotations) {
        if (annotation.type !== 'rectangle' || annotation.points.length < 4) continue;
        
        // 计算矩形的边界
        const points = annotation.points;
        const x1 = points[0][0] * ratio + imgX;
        const y1 = points[0][1] * ratio + imgY;
        const x2 = points[2][0] * ratio + imgX;
        const y2 = points[2][1] * ratio + imgY;
        
        // 检查鼠标是否在矩形内部
        if (canvasX >= x1 && canvasX <= x2 && canvasY >= y1 && canvasY <= y2) {
            return annotation;
        }
    }
    return null;
}

// 处理鼠标移动事件
function handleMouseMove(e) {
    if (!currentImage) return;
    
    const rect = e.target.getBoundingClientRect();
    const canvas = e.target;
    
    // 获取图片的实际尺寸和位置
    const img = imageCache.get(currentImage);
    if (!img) return;
    
    // 计算图片在画布上的显示尺寸和位置（自适应居中）
    const container = document.getElementById('imageCanvasContainer');
    const maxWidth = container.clientWidth - 20;
    const maxHeight = container.clientHeight - 20;
    const ratio = Math.min(maxWidth / img.width, maxHeight / img.height);
    const scaledWidth = img.width * ratio;
    const scaledHeight = img.height * ratio;
    const imgX = (container.clientWidth - scaledWidth) / 2;
    const imgY = (container.clientHeight - scaledHeight) / 2;
    
    // 计算鼠标在画布上的坐标
    const canvasX = e.clientX - rect.left;
    const canvasY = e.clientY - rect.top;
    
    // 计算鼠标在图片上的实际坐标
    const x = (canvasX - imgX) / ratio;
    const y = (canvasY - imgY) / ratio;
    
    // 处理调整大小
    if (isResizing && selectedAnnotationId && resizeHandle) {
        if (!lastMousePos) return;
        
        const dx = (e.clientX - lastMousePos.x) / ratio;
        const dy = (e.clientY - lastMousePos.y) / ratio;
        
        resizeAnnotation(selectedAnnotationId, resizeHandle, dx, dy);
        lastMousePos = {x: e.clientX, y: e.clientY};
        redrawCanvas();
        return;
    }
    
    // 处理移动标注
    if (isMoving && selectedAnnotationId) {
        if (!lastMousePos) return;
        
        const dx = (e.clientX - lastMousePos.x) / ratio;
        const dy = (e.clientY - lastMousePos.y) / ratio;
        
        moveAnnotation(selectedAnnotationId, dx, dy);
        lastMousePos = {x: e.clientX, y: e.clientY};
        redrawCanvas();
        return;
    }
    
    // 处理多边形绘制过程中的鼠标移动
    if (isPolygonDrawing) {
        // 更新当前鼠标位置，用于绘制从最后一个顶点到当前鼠标位置的连线
        currentPoint = {x: x, y: y};
        redrawCanvas();
        return;
    }
    
    // 处理矩形绘制过程中的鼠标移动
    if (isDrawing) {
        // 更新当前点
        currentPoint = {x: x, y: y};
        redrawCanvas();
    } else if (currentTool === 'rect' || currentTool === 'polygon') {
        // 绘制十字引导线，但不重绘画布以避免闪烁
        drawCrosshair(e);
    }
}

// 调整标注大小
function resizeAnnotation(annotationId, handle, dx, dy) {
    const annotation = currentAnnotations.find(a => a.id === annotationId);
    if (!annotation || annotation.type !== 'rectangle') return;
    
    const points = annotation.points;
    if (points.length < 4) return;
    
    // 计算当前矩形的边界
    let x1 = points[0][0];
    let y1 = points[0][1];
    let x2 = points[2][0];
    let y2 = points[2][1];
    
    // 根据不同的控制点调整矩形大小
    switch (handle) {
        case 'nw': // 左上
            x1 += dx;
            y1 += dy;
            break;
        case 'n': // 上中
            y1 += dy;
            break;
        case 'ne': // 右上
            x2 += dx;
            y1 += dy;
            break;
        case 'e': // 右中
            x2 += dx;
            break;
        case 'se': // 右下
            x2 += dx;
            y2 += dy;
            break;
        case 's': // 下中
            y2 += dy;
            break;
        case 'sw': // 左下
            x1 += dx;
            y2 += dy;
            break;
        case 'w': // 左中
            x1 += dx;
            break;
    }
    
    // 确保矩形的宽高为正
    if (x2 < x1) [x1, x2] = [x2, x1];
    if (y2 < y1) [y1, y2] = [y2, y1];
    
    // 更新矩形的四个角点
    annotation.points = [
        [x1, y1],
        [x2, y1],
        [x2, y2],
        [x1, y2]
    ];
}

// 移动标注
function moveAnnotation(annotationId, dx, dy) {
    const annotation = currentAnnotations.find(a => a.id === annotationId);
    if (!annotation) return;
    
    // 更新所有点的坐标
    annotation.points = annotation.points.map(point => [
        point[0] + dx,
        point[1] + dy
    ]);
}

// 处理鼠标抬起事件
function handleMouseUp(e) {
    if (!currentImage) return;
    
    // 结束调整大小
    if (isResizing) {
        isResizing = false;
        resizeHandle = null;
        saveAnnotationsSilent();
        return;
    }
    
    // 结束移动标注
    if (isMoving) {
        isMoving = false;
        saveAnnotationsSilent();
        return;
    }
    
    // 处理矩形绘制完成
    if (isDrawing && startPoint && currentPoint && currentTool === 'rect') {
        // 矩形工具 - 创建矩形标注
        const width = Math.abs(currentPoint.x - startPoint.x);
        const height = Math.abs(currentPoint.y - startPoint.y);
        const minX = Math.min(startPoint.x, currentPoint.x);
        const minY = Math.min(startPoint.y, currentPoint.y);
        
        if (width > 5 && height > 5) { // 避免误触创建太小的矩形
            const selectedClass = getSelectedClass();
            if (selectedClass) {
                const annotation = {
                    id: Date.now(),
                    class: selectedClass.name,
                    points: [
                        [minX, minY],
                        [minX + width, minY],
                        [minX + width, minY + height],
                        [minX, minY + height]
                    ],
                    type: 'rectangle'
                };
                currentAnnotations.push(annotation);
                saveAnnotationsSilent();
                updateAnnotationList();
            }
        }
        isDrawing = false;
        startPoint = null;
        currentPoint = null;
        redrawCanvas();
    }
}

// 处理双击事件，完成多边形绘制
function handleDoubleClick(e) {
    if (!currentImage || currentTool !== 'polygon' || !isPolygonDrawing || polygonPoints.length < 3) return;
    
    // 完成多边形绘制
    const selectedClass = getSelectedClass();
    if (selectedClass) {
        // 将多边形顶点转换为所需格式
        const points = polygonPoints.map(point => [point.x, point.y]);
        
        const annotation = {
            id: Date.now(),
            class: selectedClass.name,
            points: points,
            type: 'polygon'
        };
        
        currentAnnotations.push(annotation);
        saveAnnotationsSilent();
        updateAnnotationListDebounced();
    }
    
    // 重置多边形绘制状态
    isPolygonDrawing = false;
    polygonPoints = [];
    currentPoint = null;
    
    redrawCanvas();
}

// 处理鼠标离开画布事件
function handleMouseLeave() {
    if (isDrawing) {
        isDrawing = false;
        startPoint = null;
        currentPoint = null;
        redrawCanvas();
    }
    
    // 如果正在绘制多边形，重置绘制状态
    if (isPolygonDrawing) {
        isPolygonDrawing = false;
        polygonPoints = [];
        currentPoint = null;
        redrawCanvas();
    }
}

// 获取选中的类别
function getSelectedClass() {
    const selectedElement = document.querySelector('.class-item.selected');
    if (!selectedElement) return null;
    
    const className = selectedElement.querySelector('.class-name').textContent;
    return classes.find(c => c.name === className);
}

// 重绘画布
function redrawCanvas() {
    const canvas = document.getElementById('imageCanvas');
    const ctx = canvas.getContext('2d');
    const container = document.getElementById('imageCanvasContainer');
    
    // 设置画布尺寸为容器大小
    canvas.width = container.clientWidth;
    canvas.height = container.clientHeight;
    
    // 清空画布
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    
    if (!currentImage) return;
    
    // 使用图像缓存避免重复加载
    if (!imageCache.has(currentImage)) {
        const img = new Image();
        img.onload = function() {
            imageCache.set(currentImage, img);
            drawImageAndAnnotations(ctx, img, container);
        };
        img.src = `/api/image/${currentImage}`;
    } else {
        const img = imageCache.get(currentImage);
        drawImageAndAnnotations(ctx, img, container);
    }
}

function drawImageAndAnnotations(ctx, img, container) {
    // 计算图片在画布上的显示尺寸和位置（自适应居中）
    const maxWidth = container.clientWidth - 20;
    const maxHeight = container.clientHeight - 20;
    const ratio = Math.min(maxWidth / img.width, maxHeight / img.height);
    const scaledWidth = img.width * ratio;
    const scaledHeight = img.height * ratio;
    const imgX = (container.clientWidth - scaledWidth) / 2;
    const imgY = (container.clientHeight - scaledHeight) / 2;
    
    // 绘制图片
    ctx.drawImage(img, imgX, imgY, scaledWidth, scaledHeight);
    
    // 绘制所有标注
    currentAnnotations.forEach(annotation => {
        drawAnnotation(ctx, annotation, ratio, ratio, imgX, imgY);
    });
    
    // 绘制当前正在绘制的形状
    if (isDrawing && startPoint && currentPoint && currentTool === 'rect') {
        // 设置绘制样式为实线
        ctx.strokeStyle = '#ff0000';
        ctx.lineWidth = 2;
        ctx.setLineDash([]); // 使用实线而不是虚线
        
        // 计算实际绘制的矩形坐标（考虑缩放和偏移）
        const rectX = startPoint.x * ratio + imgX;
        const rectY = startPoint.y * ratio + imgY;
        const rectWidth = (currentPoint.x - startPoint.x) * ratio;
        const rectHeight = (currentPoint.y - startPoint.y) * ratio;
        
        ctx.strokeRect(rectX, rectY, rectWidth, rectHeight);
        
        // 绘制控制点
        drawControlPoints(ctx, 
            {x: startPoint.x * ratio + imgX, y: startPoint.y * ratio + imgY}, 
            {x: currentPoint.x * ratio + imgX, y: currentPoint.y * ratio + imgY}
        );
    }
    
    // 绘制多边形
    if (isPolygonDrawing && polygonPoints.length > 0) {
        ctx.save();
        ctx.strokeStyle = '#ff0000';
        ctx.lineWidth = 2;
        ctx.setLineDash([]);
        
        // 1. 绘制连线（从第一个点到当前鼠标位置）
        ctx.beginPath();
        
        // 绘制已添加的顶点之间的连线
        for (let i = 0; i < polygonPoints.length; i++) {
            const point = polygonPoints[i];
            const canvasX = point.x * ratio + imgX;
            const canvasY = point.y * ratio + imgY;
            
            if (i === 0) {
                ctx.moveTo(canvasX, canvasY);
            } else {
                ctx.lineTo(canvasX, canvasY);
            }
        }
        
        // 绘制从最后一个点到当前鼠标位置的连线
        if (currentPoint && polygonPoints.length > 0) {
            const lastPoint = polygonPoints[polygonPoints.length - 1];
            const lastCanvasX = lastPoint.x * ratio + imgX;
            const lastCanvasY = lastPoint.y * ratio + imgY;
            const currentCanvasX = currentPoint.x * ratio + imgX;
            const currentCanvasY = currentPoint.y * ratio + imgY;
            
            ctx.moveTo(lastCanvasX, lastCanvasY);
            ctx.lineTo(currentCanvasX, currentCanvasY);
        }
        
        ctx.stroke();
        
        // 2. 绘制已添加的顶点
        ctx.fillStyle = '#ff0000';
        for (const point of polygonPoints) {
            const canvasX = point.x * ratio + imgX;
            const canvasY = point.y * ratio + imgY;
            
            ctx.beginPath();
            ctx.arc(canvasX, canvasY, 4, 0, Math.PI * 2);
            ctx.fill();
        }
        
        ctx.restore();
    }
}

// 绘制控制点
function drawControlPoints(ctx, startPoint, currentPoint) {
    if (!startPoint || !currentPoint) return;
    
    const pointRadius = 4;
    ctx.fillStyle = '#ff0000';
    
    // 起始点
    ctx.beginPath();
    ctx.arc(startPoint.x, startPoint.y, pointRadius, 0, Math.PI * 2);
    ctx.fill();
    
    // 当前点
    ctx.beginPath();
    ctx.arc(currentPoint.x, currentPoint.y, pointRadius, 0, Math.PI * 2);
    ctx.fill();
}

// 绘制所有标注
function drawAnnotations(ctx, scaleX = 1, scaleY = 1, offsetX = 0, offsetY = 0) {
    currentAnnotations.forEach(annotation => {
        drawAnnotation(ctx, annotation, scaleX, scaleY, offsetX, offsetY);
    });
}

// 绘制单个标注
function drawAnnotation(ctx, annotation, scaleX = 1, scaleY = 1, offsetX = 0, offsetY = 0) {
    if (!annotation.points || annotation.points.length === 0) return;
    
    const classInfo = classes.find(c => c.name === annotation.class);
    const color = classInfo ? classInfo.color : '#ff0000';
    
    // 检查是否为选中状态
    const isSelected = annotation.id === selectedAnnotationId;
    
    ctx.beginPath();
    ctx.moveTo(annotation.points[0][0] * scaleX + offsetX, annotation.points[0][1] * scaleY + offsetY);
    
    for (let i = 1; i < annotation.points.length; i++) {
        ctx.lineTo(annotation.points[i][0] * scaleX + offsetX, annotation.points[i][1] * scaleY + offsetY);
    }
    
    if (annotation.type === 'rectangle' || annotation.points.length > 2) {
        ctx.closePath();
        ctx.fillStyle = color + '40'; // 半透明填充
        ctx.fill();
    }
    
    // 绘制边框
    ctx.strokeStyle = isSelected ? '#ff0000' : color;
    ctx.lineWidth = isSelected ? 3 : 2;
    ctx.stroke();
    
    // 绘制标签名
    if (annotation.points.length > 0) {
        const textX = annotation.points[0][0] * scaleX + offsetX;
        const textY = annotation.points[0][1] * scaleY + offsetY - 5;
        
        ctx.fillStyle = isSelected ? '#ff0000' : color;
        ctx.font = '14px Arial';
        ctx.fillText(annotation.class, textX, textY);
    }
    
    // 如果是选中状态，绘制控制点
    if (isSelected && annotation.type === 'rectangle') {
        drawResizeHandles(ctx, annotation, scaleX, scaleY, offsetX, offsetY);
    }
}

// 绘制调整大小的控制点
function drawResizeHandles(ctx, annotation, scaleX, scaleY, offsetX, offsetY) {
    const points = annotation.points;
    if (points.length < 4) return;
    
    // 计算矩形的四个角点
    const x1 = points[0][0] * scaleX + offsetX;
    const y1 = points[0][1] * scaleY + offsetY;
    const x2 = points[2][0] * scaleX + offsetX;
    const y2 = points[2][1] * scaleY + offsetY;
    
    // 控制点位置
    const handles = [
        { x: x1, y: y1, type: 'nw' }, // 左上
        { x: (x1 + x2) / 2, y: y1, type: 'n' }, // 上中
        { x: x2, y: y1, type: 'ne' }, // 右上
        { x: x2, y: (y1 + y2) / 2, type: 'e' }, // 右中
        { x: x2, y: y2, type: 'se' }, // 右下
        { x: (x1 + x2) / 2, y: y2, type: 's' }, // 下中
        { x: x1, y: y2, type: 'sw' }, // 左下
        { x: x1, y: (y1 + y2) / 2, type: 'w' }  // 左中
    ];
    
    // 绘制控制点
    ctx.fillStyle = '#ffffff';
    ctx.strokeStyle = '#ff0000';
    ctx.lineWidth = 1;
    
    handles.forEach(handle => {
        ctx.beginPath();
        ctx.arc(handle.x, handle.y, 6, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
    });
}

// 加载类别
function loadClasses() {
    fetch('/api/classes')
        .then(response => response.json())
        .then(data => {
            classes = data;
            updateClassList();
            updateWorkflowGuide();
        })
        .catch(error => console.error('加载类别失败:', error));
}

// 更新类别列表
function updateClassList() {
    const classList = document.getElementById('classList');
    classList.innerHTML = '';
    
    classes.forEach((cls, index) => {
        const li = document.createElement('li');
        li.className = 'class-item';
        // 设置CSS变量，用于背景色
        li.style.setProperty('--class-color', cls.color);
        // 显示数字序号（1-9显示数字，超过9显示-）
        const shortcutKey = index < 9 ? (index + 1) : '-';
        li.innerHTML = `
            <span class="class-shortcut">${shortcutKey}</span>
            <span class="class-name">${cls.name}</span>
            <div class="class-actions">
                <button class="class-edit-btn" data-index="${index}">
                    <i class="fas fa-pencil-alt"></i>
                </button>
            </div>
            <button class="class-delete-btn" data-index="${index}">
                <i class="fas fa-times"></i>
            </button>
        `;
        classList.appendChild(li);
    });
    
    // 添加事件监听器
    document.querySelectorAll('.class-item').forEach((item, index) => {
        // 点击选中类别
        item.addEventListener('click', function() {
            document.querySelectorAll('.class-item').forEach(i => i.classList.remove('selected'));
            this.classList.add('selected');
        });
        
        // 编辑按钮事件
        const editBtn = item.querySelector('.class-edit-btn');
        if (editBtn) {
            editBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                editClass(index);
            });
        }
        
        // 删除按钮事件
        const deleteBtn = item.querySelector('.class-delete-btn');
        if (deleteBtn) {
            deleteBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                deleteClass(index);
            });
        }
    });
    
    // 默认选中第一个类别
    const firstClassItem = document.querySelector('.class-item');
    if (firstClassItem) {
        firstClassItem.classList.add('selected');
    }
}

// 添加类别
function addClass() {
    const nameInput = document.getElementById('newClassInput');
    const colorInput = document.getElementById('newClassColor');
    const name = nameInput.value.trim();
    
    if (!name) {
        showToast('请输入标签名称');
        return;
    }
    
    // 检查是否已存在同名类别
    if (classes.some(cls => cls.name === name)) {
        showToast('类别名称已存在');
        return;
    }
    
    const newClass = {
        name: name,
        color: colorInput.value
    };
    
    classes.push(newClass);
    updateClassList();
    saveClasses();
    
    // 清空输入框
    nameInput.value = '';
}

// 编辑类别
function editClass(index) {
    const cls = classes[index];
    document.getElementById('editClassIndex').value = index;
    document.getElementById('editClassName').value = cls.name;
    document.getElementById('editClassColor').value = cls.color;
    
    const modal = document.getElementById('editClassModal');
    modal.style.display = 'block';
}

// 处理类别编辑表单提交
function handleEditClass(e) {
    e.preventDefault();
    
    const index = document.getElementById('editClassIndex').value;
    const name = document.getElementById('editClassName').value.trim();
    const color = document.getElementById('editClassColor').value;
    
    if (!name) {
        showToast('请输入类别名称');
        return;
    }
    
    // 检查是否与其他类别重名
    if (classes.some((cls, i) => i != index && cls.name === name)) {
        showToast('类别名称已存在');
        return;
    }
    
    classes[index] = {
        name: name,
        color: color
    };
    
    updateClassList();
    saveClasses();
    
    // 关闭模态框
    document.getElementById('editClassModal').style.display = 'none';
}

// 删除类别
function deleteClass(index) {
    if (confirm(`确定要删除类别 "${classes[index].name}" 吗？`)) {
        classes.splice(index, 1);
        updateClassList();
        saveClasses();
    }
}

// 保存类别
function saveClasses() {
    fetch('/api/classes', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(classes)
    }).catch(error => console.error('保存类别失败:', error));
}

// 加载图片列表
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
    
    // 调试：检查当前图片的标注数量
    const currentImageData = images.find(img => img.name === currentImage);
    if (currentImageData) {
        console.log('当前图片标注数量:', currentImageData.name, currentImageData.annotation_count);
    }
    
    images.forEach((image, index) => {
        const li = document.createElement('li');
        li.className = 'image-item';
        li.dataset.image = image.name;
        
        // 检查是否有标注
        const hasAnnotations = image.annotation_count > 0;
        
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
            <div class="image-name" title="${image.name}">${image.name}</div>
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

function updateWorkflowGuide() {
    const hintEl = document.getElementById('workflowHint');
    const goalEl = document.getElementById('workflowGoal');
    const dodEl = document.getElementById('workflowDod');
    const stepIds = ['wfStep1', 'wfStep2', 'wfStep3', 'wfStep4', 'wfStep5', 'wfStep6'];
    if (!hintEl || !goalEl || !dodEl || !document.getElementById(stepIds[0])) return;

    const images = window.allImages || [];
    const total = images.length;
    const annotated = images.filter(img => img.annotation_count > 0).length;
    const hasClasses = Array.isArray(classes) && classes.length > 0;

    stepIds.forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.classList.remove('active');
        el.classList.remove('done');
    });

    const step1 = document.getElementById('wfStep1');
    const step2 = document.getElementById('wfStep2');
    const step3 = document.getElementById('wfStep3');
    const step4 = document.getElementById('wfStep4');
    const step5 = document.getElementById('wfStep5');
    const step6 = document.getElementById('wfStep6');

    if (total > 0) step1.classList.add('done');
    if (hasClasses) step2.classList.add('done');
    if (annotated >= COLD_START_MIN_ANNOTATED) {
        step3.classList.add('done');
        step4.classList.add('done');
    }
    if (annotated >= COLD_START_MIN_ANNOTATED) step5.classList.add('active');
    if (annotated >= 150) step6.classList.add('active');

    let suggestedStep = 1;
    if (total === 0) {
        step1.classList.add('active');
        hintEl.textContent = '当前建议：先导入视频/图片并抽帧生成样本';
        suggestedStep = 1;
    } else if (!hasClasses) {
        step2.classList.add('active');
        hintEl.textContent = '当前建议：先创建标签体系，再开始标注';
        suggestedStep = 2;
    } else if (annotated < COLD_START_MIN_ANNOTATED) {
        step3.classList.add('active');
        hintEl.textContent = `当前建议：先用 SAM3 批量预标，再人工兜底（已 ${annotated}/${COLD_START_MIN_ANNOTATED}）`;
        suggestedStep = 3;
    } else if (annotated < 150) {
        step5.classList.add('active');
        hintEl.textContent = `当前建议：可用 v1.0 进行AI标注并人工复核（已标注 ${annotated} 张）`;
        suggestedStep = 5;
    } else {
        step6.classList.add('active');
        hintEl.textContent = '当前建议：进入增量训练并导出稳定数据集版本';
        suggestedStep = 6;
    }

    const focusStep = workflowSelectedStep || suggestedStep;
    renderWorkflowStepDetail(focusStep, {
        total,
        annotated,
        hasClasses
    });
    applyStepSpecificLayout(focusStep);
}

function setupWorkflowStepClickEvents() {
    const stepIds = ['wfStep1', 'wfStep2', 'wfStep3', 'wfStep4', 'wfStep5', 'wfStep6'];
    stepIds.forEach((stepId, idx) => {
        const el = document.getElementById(stepId);
        if (!el) return;
        el.style.cursor = 'pointer';
        el.addEventListener('click', () => {
            workflowSelectedStep = idx + 1;
            updateWorkflowGuide();
        });
    });
}

function renderWorkflowStepDetail(step, snapshot) {
    const goalEl = document.getElementById('workflowGoal');
    const dodEl = document.getElementById('workflowDod');
    const nextBtn = document.getElementById('workflowNextBtn');
    if (!goalEl || !dodEl || !nextBtn) return;

    const mapping = {
        1: {
            title: '步骤1：导入数据',
            goal: '目标：导入首批可标注样本',
            dod: '完成标准：图片列表非空',
            actionLabel: '去导入数据',
            taskDesc: '导入视频或图片并抽帧，先把样本池建立起来。',
            checklist: [
                { text: '已导入至少1个视频或图片目录', done: snapshot.total > 0 },
                { text: '图片列表可浏览和搜索', done: snapshot.total > 0 },
                { text: '准备进入标签定义', done: snapshot.total > 0 }
            ]
        },
        2: {
            title: '步骤2：标签规范',
            goal: '目标：建立稳定标签体系',
            dod: `完成标准：至少 1 个标签（当前 ${snapshot.hasClasses ? '已完成' : '未完成'}）`,
            actionLabel: '去设置标签',
            taskDesc: '先定义清晰类别边界，后续AI标注质量才稳定。',
            checklist: [
                { text: '创建至少1个有效标签', done: snapshot.hasClasses },
                { text: '确认标签命名规范统一', done: snapshot.hasClasses },
                { text: '准备进入冷启动标注', done: snapshot.hasClasses }
            ]
        },
        3: {
            title: `步骤3：SAM3预标+人工兜底（${COLD_START_MIN_ANNOTATED}张）`,
            goal: '目标：用 SAM3 快速冷启动并人工兜底',
            dod: `完成标准：已标注 >= ${COLD_START_MIN_ANNOTATED} 张（当前 ${snapshot.annotated}/${COLD_START_MIN_ANNOTATED}）`,
            actionLabel: '去SAM3预标',
            taskDesc: '先用 SAM3 批量预标，再逐张人工复核，保证质量与效率。',
            checklist: [
                { text: 'AI引擎选择为 SAM3 并完成一轮预标', done: snapshot.annotated > 0 },
                { text: `已标注数量达到${COLD_START_MIN_ANNOTATED}张（当前 ${snapshot.annotated}）`, done: snapshot.annotated >= COLD_START_MIN_ANNOTATED },
                { text: '抽检关键类别并人工修正漏标/误标', done: snapshot.annotated >= COLD_START_MIN_ANNOTATED },
                { text: '可启动 v1.0 训练', done: snapshot.annotated >= COLD_START_MIN_ANNOTATED }
            ]
        },
        4: {
            title: '步骤4：训练v1.0',
            goal: '目标：训练第一版业务模型 v1.0',
            dod: '完成标准：训练任务完成且可设为生产模型',
            actionLabel: '去训练中心',
            taskDesc: '在训练中心启动首轮训练，观察任务状态与模型产出。',
            checklist: [
                { text: '打开训练中心并检查准备度', done: false },
                { text: '启动初代训练任务', done: false },
                { text: '任务完成并可用于AI预标', done: false }
            ]
        },
        5: {
            title: '步骤5：AI标注+复核',
            goal: '目标：用业务模型(v1.0+)批量预标并人工复核',
            dod: '完成标准：批量标注执行且样本经人工审查',
            actionLabel: '去AI标注',
            taskDesc: '此阶段优先选择业务模型（YOLO11），持续提效并保持人工兜底。',
            checklist: [
                { text: '已选择业务模型并设置置信度', done: false },
                { text: '已执行批量预标注', done: false },
                { text: '已完成人工复核与修补', done: false }
            ]
        },
        6: {
            title: '步骤6：增量训练与导出',
            goal: '目标：增量训练并导出版本化数据集',
            dod: '完成标准：完成一次增量训练并导出数据',
            actionLabel: '去导出数据',
            taskDesc: '进入稳定迭代，输出可复用的版本化数据资产。',
            checklist: [
                { text: '增量训练任务已启动并完成', done: false },
                { text: '核心类别效果达到预期', done: false },
                { text: '已导出可复现数据集版本', done: false }
            ]
        }
    };

    const conf = mapping[step] || mapping[1];
    goalEl.textContent = conf.goal;
    dodEl.textContent = conf.dod;
    nextBtn.textContent = conf.actionLabel;
    nextBtn.dataset.step = String(step);
    const primaryBtn = document.getElementById('workflowPrimaryActionBtn');
    if (primaryBtn) {
        primaryBtn.textContent = conf.actionLabel;
        primaryBtn.dataset.step = String(step);
    }
    renderWorkflowTaskCard(conf);
    highlightStepButtons(step);
}

function runWorkflowNextAction() {
    const nextBtn = document.getElementById('workflowNextBtn');
    const primaryBtn = document.getElementById('workflowPrimaryActionBtn');
    const step = Number(primaryBtn?.dataset?.step || nextBtn?.dataset?.step || '1');
    switch (step) {
        case 1:
            showDatasetModal();
            break;
        case 2:
            showToast('在右侧“标签管理”中添加标签，至少创建1个');
            break;
        case 3:
            showAiAnnotateModal('sam3');
            showToast('冷启动建议选择 SAM3，先批量预标再人工兜底');
            break;
        case 4:
            showTrainingCenterModal();
            break;
        case 5:
            showAiAnnotateModal('yolo11');
            showToast('当前阶段建议使用业务模型（YOLO11）批量预标并人工复核');
            break;
        case 6:
            showExportModal();
            break;
        default:
            showDatasetModal();
            break;
    }
}

function renderWorkflowTaskCard(conf) {
    const titleEl = document.getElementById('workflowTaskTitle');
    const descEl = document.getElementById('workflowTaskDesc');
    const checklistEl = document.getElementById('workflowChecklist');
    if (!titleEl || !descEl || !checklistEl) return;

    titleEl.textContent = conf.title || '';
    descEl.textContent = conf.taskDesc || '';
    checklistEl.innerHTML = '';
    (conf.checklist || []).forEach(item => {
        const li = document.createElement('li');
        li.textContent = `${item.done ? '✔' : '○'} ${item.text}`;
        if (item.done) li.classList.add('done');
        checklistEl.appendChild(li);
    });
}

function highlightStepButtons(step) {
    const buttonIds = ['openFolderBtn', 'aiAnnotateToggle', 'trainCenterBtn', 'exportBtn', 'saveAnnotationBtn', 'quickImportBtn', 'quickAiBtn', 'quickTrainBtn', 'quickExportBtn'];
    buttonIds.forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.classList.remove('step-focused-btn');
    });
    const highlightMap = {
        1: ['openFolderBtn', 'quickImportBtn'],
        2: [],
        3: ['saveAnnotationBtn'],
        4: ['trainCenterBtn', 'quickTrainBtn'],
        5: ['aiAnnotateToggle', 'quickAiBtn'],
        6: ['exportBtn', 'quickExportBtn']
    };
    (highlightMap[step] || []).forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.classList.add('step-focused-btn');
    });
}

function applyStepSpecificLayout(step) {
    const classSection = document.getElementById('classToolSection');
    const annotationSection = document.getElementById('annotationToolSection');
    if (!classSection || !annotationSection) return;

    classSection.classList.remove('hidden');
    annotationSection.classList.remove('hidden');

    if (step === 1 || step === 4 || step === 6) {
        classSection.classList.add('hidden');
        annotationSection.classList.add('hidden');
    } else if (step === 2) {
        classSection.classList.remove('hidden');
        annotationSection.classList.add('hidden');
    } else {
        classSection.classList.remove('hidden');
        annotationSection.classList.remove('hidden');
    }
}

// 筛选图片
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
function updateAnnotationList() {
    const annotationList = document.getElementById('currentAnnotations');
    annotationList.innerHTML = '';
    
    currentAnnotations.forEach((annotation, index) => {
        const li = document.createElement('li');
        li.className = `annotation-item ${annotation.id === selectedAnnotationId ? 'selected' : ''}`;
        li.dataset.annotationId = annotation.id;
        li.innerHTML = `
            <div class="annotation-color" style="background-color: ${getClassColor(annotation.class)};"></div>
            <span class="annotation-class">${annotation.class}</span>
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
function getClassColor(className) {
    const cls = classes.find(c => c.name === className);
    return cls ? cls.color : '#ff0000';
}

// 删除标注
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
    
    console.log('正在保存标注...', currentImage, currentAnnotations);
    
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
        console.log('保存成功，服务器返回:', data);
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
            if (isInstalled) {
                // 显示详细安装信息
                const installTime = data.install_time || '未知';
                const hardware = data.has_cuda ? 'CUDA (GPU)' : 'CPU';
                installInfoElement.innerHTML = `
                    <p style="margin: 5px 0;"><strong>安装时间:</strong> ${installTime}</p>
                    <p style="margin: 5px 0;"><strong>硬件支持:</strong> ${hardware}</p>
                `;
                installInfoElement.style.display = 'block';
                
                // 更新按钮状态
                if (modelsSection) {
                    modelsSection.style.opacity = '1';
                    modelsSection.style.pointerEvents = 'auto';
                }
                if (downloadModelsBtn) downloadModelsBtn.disabled = false;
                if (refreshModelsBtn) refreshModelsBtn.disabled = false;
            } else {
                // 隐藏安装信息
                installInfoElement.innerHTML = '';
                installInfoElement.style.display = 'none';
                
                // 更新按钮状态
                if (modelsSection) {
                    modelsSection.style.opacity = '0.5';
                    modelsSection.style.pointerEvents = 'none';
                }
                if (downloadModelsBtn) downloadModelsBtn.disabled = true;
                if (refreshModelsBtn) refreshModelsBtn.disabled = true;
            }
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
    const eventSource = new EventSource(`/api/download-models?models=${selectedModels.join(',')}&install_path=${encodeURIComponent(installPath)}`);
    
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
                    modelItem.innerHTML = `
                        <i class="fas fa-file-code"></i>
                        <span class="model-name">${model}</span>
                        <button class="delete-model-btn" onclick="deleteModel('${model}')">
                            <i class="fas fa-times"></i>
                        </button>
                    `;
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
function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
}

// 高亮拖放区域
function highlight(e) {
    const dropZone = document.getElementById('modelDropZone');
    dropZone.style.borderColor = '#339af0';
    dropZone.style.backgroundColor = '#e3f2fd';
}

// 取消高亮拖放区域
function unhighlight(e) {
    const dropZone = document.getElementById('modelDropZone');
    dropZone.style.borderColor = '#ced4da';
    dropZone.style.backgroundColor = '#f8f9fa';
}

// 处理文件拖放
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
function handleExport(e) {
    e.preventDefault();
    
    // 获取表单数据
    const formData = new FormData(e.target);
    const trainRatio = parseFloat(formData.get('trainRatio'));
    const valRatio = parseFloat(formData.get('valRatio'));
    const testRatio = parseFloat(formData.get('testRatio'));

    console.log("trainRatio:", typeof trainRatio, trainRatio);
    console.log("valRatio:", typeof valRatio, valRatio);
    console.log("testRatio:", typeof testRatio,testRatio)


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
        console.log('保存快捷键触发, isInInput:', isInInput);
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
function selectClassByIndex(index) {
    if (index < 0 || index >= classes.length) {
        showToast(`标签 ${index + 1} 不存在`);
        return;
    }
    
    // 移除所有选中状态
    document.querySelectorAll('.class-item').forEach(item => {
        item.classList.remove('selected');
    });
    
    // 选中对应的标签
    const classItems = document.querySelectorAll('.class-item');
    if (classItems[index]) {
        classItems[index].classList.add('selected');
        showToast(`已切换到: ${classes[index].name}`);
    }
}

// 删除选中的标注框
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
function showToast(message) {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.style.display = 'block';
    toast.classList.add('show');

    clearTimeout(toast._hideTimer);
    toast._hideTimer = setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => { toast.style.display = 'none'; }, 300);
    }, 3000);
}

// 页面卸载前确认
window.addEventListener('beforeunload', function(e) {
    // 如果有未保存的更改，显示确认提示
    // 这里可以根据需要实现
});

// 绘制十字引导线 - 移除直接在主画布上绘制的逻辑，避免重影
function drawCrosshair(e) {
    // 不再直接在画布上绘制十字线，避免重影问题
    // 重绘画布时会清除所有临时绘制
    return;
}

// 切换手风琴折叠状态
function toggleAccordion(header) {
    const item = header.parentElement;
    item.classList.toggle('active');
    const body = item.querySelector('.accordion-body');
    if (item.classList.contains('active')) {
        body.style.display = 'block';
    } else {
        body.style.display = 'none';
    }
}

// ==================== AI标注功能 ====================

// 切换AI标注状态
function toggleAiAnnotate() {
    if (aiAnnotateEnabled) {
        // 关闭AI标注
        disableAiAnnotate();
    } else {
        // 显示AI标注设置模态框
        showAiAnnotateModal();
    }
}

// 显示AI标注模态框
function showAiAnnotateModal(preferredEngine = null) {
    const modal = document.getElementById('aiAnnotateModal');
    modal.style.display = 'block';

    if (preferredEngine === 'sam3' || preferredEngine === 'yolo11') {
        aiAnnotateEngine = preferredEngine;
    }

    const engineSelect = document.getElementById('aiEngineSelect');
    if (engineSelect) {
        engineSelect.value = aiAnnotateEngine;
    }
    syncWorldClassesInputFromCurrent();
    onAiEngineChanged();
    
    // 加载可用模型列表
    loadAiModels();
    
    // 更新批量标注范围信息
    updateBatchRangeInfo();
    
    // 设置默认范围为全部图片
    const totalImages = window.allImages ? window.allImages.length : 0;
    const endInput = document.getElementById('batchEndIndex');
    if (endInput && totalImages > 0) {
        endInput.value = totalImages;
    }
    updateBatchRangeInfo();
}

function getRecommendedAiEngineForCurrentStep() {
    const nextBtn = document.getElementById('workflowNextBtn');
    const currentStep = Number(workflowSelectedStep || nextBtn?.dataset?.step || 0);
    if (currentStep === 3) return 'sam3';
    return 'yolo11';
}

function syncWorldClassesInputFromCurrent() {
    const input = document.getElementById('worldClassesInput');
    if (!input || input.value.trim()) return;
    if (Array.isArray(classes) && classes.length > 0) {
        input.value = classes.map(c => c.name).filter(Boolean).join(',');
    }
}

function getWorldClassesInput() {
    const input = document.getElementById('worldClassesInput');
    const raw = (input?.value || '').trim();
    if (raw) {
        return raw
            .replace(/，/g, ',')
            .split(',')
            .map(x => x.trim())
            .filter(Boolean);
    }
    return (classes || []).map(c => c.name).filter(Boolean);
}

function onAiEngineChanged() {
    const engineSelect = document.getElementById('aiEngineSelect');
    aiAnnotateEngine = engineSelect?.value || 'yolo11';
    const worldGroup = document.getElementById('worldClassesGroup');
    if (worldGroup) {
        worldGroup.style.display = aiAnnotateEngine === 'sam3' ? 'block' : 'none';
    }
    const startBtn = document.getElementById('aiAnnotateStartBtn');
    if (startBtn) {
        if (aiAnnotateEngine === 'sam3') {
            startBtn.innerHTML = '<i class="fas fa-magic"></i> 开始区间预标注';
        } else {
            startBtn.innerHTML = '<i class="fas fa-play"></i> 开启AI标注';
        }
    }
    loadAiModels();
}

// 加载AI模型列表
function loadAiModels() {
    const installPath = document.getElementById('yolo11InstallPath')?.value || 'plugins/yolo11';
    const modelSelect = document.getElementById('aiModelSelect');
    
    modelSelect.innerHTML = '<option value="">-- 加载中... --</option>';

    if (aiAnnotateEngine === 'sam3') {
        fetch('/api/sam3/status')
            .then(r => r.json())
            .then(status => {
                modelSelect.innerHTML = '';
                if (status.loaded) {
                    const option = document.createElement('option');
                    option.value = 'sam3';
                    option.textContent = 'SAM3 (' + (status.model_path || '').split(/[\\/]/).pop() + ')';
                    modelSelect.appendChild(option);
                    modelSelect.value = 'sam3';
                    aiAnnotateModel = 'sam3';
                } else {
                    modelSelect.innerHTML = '<option value="">-- SAM3模型未加载 --</option>';
                    showToast('SAM3模型未加载，请检查模型文件路径');
                }
            })
            .catch(() => {
                modelSelect.innerHTML = '<option value="">-- SAM3状态检查失败 --</option>';
            });
        return;
    }

    Promise.all([
        fetch(`/api/list-models?install_path=${encodeURIComponent(installPath)}`).then(response => response.json()),
        fetch('/api/models/active').then(response => response.json()).catch(() => ({}))
    ])
        .then(([modelsData, activeModel]) => {
            modelSelect.innerHTML = '<option value="">-- 请选择模型 --</option>';
            if (modelsData.models && modelsData.models.length > 0) {
                modelsData.models.forEach(model => {
                    const option = document.createElement('option');
                    option.value = model;
                    option.textContent = model;
                    modelSelect.appendChild(option);
                });

                // 优先使用当前会话已选择模型，其次用激活模型
                const preferredModel =
                    aiAnnotateModel ||
                    activeModel?.model_name ||
                    ((activeModel?.model_path || '').split(/[\\/]/).pop() || '');
                if (preferredModel && modelsData.models.includes(preferredModel)) {
                    modelSelect.value = preferredModel;
                    aiAnnotateModel = preferredModel;
                }
            } else {
                modelSelect.innerHTML = '<option value="">-- 无可用模型，请去设置里安装模型 --</option>';
                showToast('当前无可用模型，正在为你打开“设置 -> YOLO11 模型管理”');
                openSettingsToModelInstall();
            }
        })
        .catch(error => {
            console.error('加载模型列表失败:', error);
            modelSelect.innerHTML = '<option value="">-- 加载失败，请去设置里安装模型 --</option>';
            showToast('模型列表加载失败，正在为你打开“设置 -> YOLO11 模型管理”');
            openSettingsToModelInstall();
        });
}

// 设置AI标注事件
function setupAiAnnotateEvents() {
    const engineSelect = document.getElementById('aiEngineSelect');
    if (engineSelect) {
        engineSelect.addEventListener('change', onAiEngineChanged);
    }

    // 置信度滑块
    const confidenceSlider = document.getElementById('aiConfidence');
    const confidenceValue = document.getElementById('aiConfidenceValue');
    if (confidenceSlider && confidenceValue) {
        confidenceSlider.addEventListener('input', function() {
            confidenceValue.textContent = this.value;
        });
    }
    
    // AI标注表单提交
    const aiForm = document.getElementById('aiAnnotateForm');
    if (aiForm) {
        aiForm.addEventListener('submit', function(e) {
            e.preventDefault();
            enableAiAnnotate();
        });
    }
    
    // 取消按钮
    const cancelBtn = document.getElementById('aiAnnotateCancelBtn');
    if (cancelBtn) {
        cancelBtn.addEventListener('click', function() {
            document.getElementById('aiAnnotateModal').style.display = 'none';
        });
    }
    
    // 批量标注按钮
    const batchBtn = document.getElementById('aiBatchAnnotateBtn');
    if (batchBtn) {
        batchBtn.addEventListener('click', function() {
            startBatchAnnotate();
        });
    }
    
    // 批量标注取消按钮
    const batchCancelBtn = document.getElementById('batchCancelBtn');
    if (batchCancelBtn) {
        batchCancelBtn.addEventListener('click', function() {
            cancelBatchAnnotate();
        });
    }
    
    // 批量标注范围输入框事件
    const startIndexInput = document.getElementById('batchStartIndex');
    const endIndexInput = document.getElementById('batchEndIndex');
    if (startIndexInput) {
        startIndexInput.addEventListener('input', updateBatchRangeInfo);
    }
    if (endIndexInput) {
        endIndexInput.addEventListener('input', updateBatchRangeInfo);
    }

    if (engineSelect) {
        onAiEngineChanged();
    }
}

// 开启AI标注
function enableAiAnnotate() {
    const engineSelect = document.getElementById('aiEngineSelect');
    const modelSelect = document.getElementById('aiModelSelect');
    const confidenceSlider = document.getElementById('aiConfidence');
    const autoNextCheckbox = document.getElementById('aiAutoNext');
    aiAnnotateEngine = engineSelect?.value || 'yolo11';
    
    if (!modelSelect.value) {
        showToast('无可用模型，请去“设置 -> YOLO11 模型管理”安装模型');
        openSettingsToModelInstall();
        return;
    }

    if (aiAnnotateEngine === 'sam3') {
        const worldClasses = getWorldClassesInput();
        if (!worldClasses.length) {
            showToast('请先输入至少一个目标类（如 base,frame,mirror,screw）');
            return;
        }
        // SAM3 默认走批量预标注（按区间），避免用户误以为”开启AI标注”会自动处理多张
        showToast('SAM3 将按你设置的区间执行批量预标注，请稍候...');
        startBatchAnnotate();
        return;
    }
    
    // 保存设置
    aiAnnotateModel = modelSelect.value;
    aiAnnotateConfidence = parseFloat(confidenceSlider.value);
    aiAutoNext = autoNextCheckbox.checked;
    if (aiAutoNext) {
        const range = getAiRangeConfig();
        aiAutoRangeStart = range.start;
        aiAutoRangeEnd = range.end;
    } else {
        aiAutoRangeStart = null;
        aiAutoRangeEnd = null;
    }
    aiAnnotateEnabled = true;
    
    // 关闭模态框
    document.getElementById('aiAnnotateModal').style.display = 'none';
    
    // 更新按钮状态
    updateAiAnnotateButton();
    
    // 显示状态栏
    showAiStatusBar();
    
    showToast(`AI标注已开启，${aiAnnotateEngine} / 模型: ${aiAnnotateModel}`);
    
    // 自动模式下，从配置的起始图开始
    if (aiAutoNext && window.allImages && window.allImages.length > 0 && Number.isFinite(aiAutoRangeStart)) {
        const startIndex = Math.max(0, aiAutoRangeStart - 1);
        const startImage = window.allImages[startIndex];
        if (startImage && currentImage !== startImage.name) {
            selectImage(startImage.name);
            return;
        }
    }

    // 如果当前有图片，立即进行AI标注
    if (currentImage) {
        performAiAnnotate();
    }
}

// 关闭AI标注
function disableAiAnnotate() {
    aiAnnotateEnabled = false;
    
    // 更新按钮状态
    updateAiAnnotateButton();
    
    // 隐藏状态栏
    hideAiStatusBar();
    
    showToast('AI标注已关闭');
}

// 更新AI标注按钮状态
function updateAiAnnotateButton() {
    const btn = document.getElementById('aiAnnotateToggle');
    if (aiAnnotateEnabled) {
        btn.classList.add('ai-active');
        btn.innerHTML = '<i class="fas fa-robot"></i> AI标注中';
        btn.style.backgroundColor = '#28a745';
        btn.style.borderColor = '#28a745';
    } else {
        btn.classList.remove('ai-active');
        btn.innerHTML = '<i class="fas fa-robot"></i> AI标注';
        btn.style.backgroundColor = '';
        btn.style.borderColor = '';
    }
}

// 显示AI状态栏 - 在导航栏内显示
function showAiStatusBar() {
    // 检查是否已存在状态信息
    let statusInfo = document.getElementById('aiStatusInfo');
    if (!statusInfo) {
        statusInfo = document.createElement('span');
        statusInfo.id = 'aiStatusInfo';
        statusInfo.className = 'ai-status-info';
        
        // 插入到导航栏logo后面
        const logo = document.querySelector('.logo');
        if (logo) {
            logo.parentNode.insertBefore(statusInfo, logo.nextSibling);
        }
    }
    
    statusInfo.innerHTML = `<i class="fas fa-robot"></i> ${aiAnnotateEngine} / ${aiAnnotateModel} | 阈值:${aiAnnotateConfidence}`;
    statusInfo.style.display = 'inline-flex';
}

// 隐藏AI状态栏
function hideAiStatusBar() {
    const statusInfo = document.getElementById('aiStatusInfo');
    if (statusInfo) {
        statusInfo.style.display = 'none';
    }
}

// 执行AI标注
function performAiAnnotate() {
    if (!aiAnnotateEnabled || !currentImage || aiAnnotating) {
        return;
    }
    
    aiAnnotating = true;
    showToast('正在进行AI标注...');
    
    const installPath = document.getElementById('yolo11InstallPath')?.value || 'plugins/yolo11';
    const worldClasses = aiAnnotateEngine === 'sam3' ? getWorldClassesInput() : [];
    const endpoint = aiAnnotateEngine === 'sam3' ? '/api/ai-annotate-sam3' : '/api/ai-annotate';
    
    fetch(endpoint, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            image_name: currentImage,
            model_name: aiAnnotateModel,
            confidence: aiAnnotateConfidence,
            install_path: installPath,
            device: 'auto',
            world_classes: worldClasses
        })
    })
    .then(response => response.json())
    .then(data => {
        aiAnnotating = false;
        
        if (data.error) {
            showToast(`AI标注失败: ${data.error}`);
            return;
        }
        
        if (data.annotations && data.annotations.length > 0) {
            // 将AI标注结果添加到当前标注
            data.annotations.forEach(ann => {
                // 生成唯一ID
                ann.id = Date.now() + Math.floor(Math.random() * 1000);
                currentAnnotations.push(ann);
            });
            
            updateAnnotationListDebounced();
            redrawCanvas();
            
            showToast(`AI标注完成，检测到 ${data.annotations.length} 个目标`);
            
            // 如果有新类别被添加，刷新类别列表
            if (data.new_classes_added) {
                loadClasses();
            }
        } else {
            showToast('AI标注完成，未检测到目标');
        }
    })
    .catch(error => {
        aiAnnotating = false;
        console.error('AI标注失败:', error);
        showToast('AI标注失败: ' + error.message);
    });
}

// 切换到下一张图片
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
function updateBatchRangeInfo() {
    const totalImages = window.allImages ? window.allImages.length : 0;
    const startInput = document.getElementById('batchStartIndex');
    const endInput = document.getElementById('batchEndIndex');
    const infoText = document.getElementById('batchRangeInfo');
    
    if (startInput && endInput && infoText) {
        // 设置最大值
        startInput.max = totalImages;
        endInput.max = totalImages;
        
        // 如果结束值大于总数，调整为总数
        if (parseInt(endInput.value) > totalImages) {
            endInput.value = totalImages;
        }
        
        const start = parseInt(startInput.value) || 1;
        const end = parseInt(endInput.value) || totalImages;
        const rangeCount = Math.max(0, end - start + 1);
        
        infoText.textContent = `共 ${totalImages} 张图片，当前选择范围: ${start}-${end} (${rangeCount}张)`;
    }
}

// 开始批量标注 - 使用新的批量API
async function startBatchAnnotate() {
    const engineSelect = document.getElementById('aiEngineSelect');
    const modelSelect = document.getElementById('aiModelSelect');
    const confidenceSlider = document.getElementById('aiConfidence');
    const skipAnnotatedCheckbox = document.getElementById('aiSkipAnnotated');
    const batchSizeSelect = document.getElementById('batchSize');
    const startIndexInput = document.getElementById('batchStartIndex');
    const endIndexInput = document.getElementById('batchEndIndex');
    
    if (!modelSelect.value) {
        showToast('请选择一个模型');
        return;
    }
    
    if (!window.allImages || window.allImages.length === 0) {
        showToast('没有图片可以标注');
        return;
    }
    
    // 获取设置
    const engine = engineSelect?.value || aiAnnotateEngine || 'yolo11';
    aiAnnotateEngine = engine;
    const model = modelSelect.value;
    const confidence = parseFloat(confidenceSlider.value);
    const skipAnnotated = skipAnnotatedCheckbox ? skipAnnotatedCheckbox.checked : false;
    const batchSizeRaw = parseInt(batchSizeSelect?.value || '10');
    const batchSize = Number.isFinite(batchSizeRaw) ? batchSizeRaw : 10;
    if (batchSize > 50) {
        showToast('单批最大限制为 50 张，请调整后重试');
        if (batchSizeSelect) batchSizeSelect.value = '50';
        return;
    }
    if (batchSize < 1) {
        showToast('批量大小必须大于 0');
        return;
    }
    const installPath = document.getElementById('yolo11InstallPath')?.value || 'plugins/yolo11';
    const worldClasses = engine === 'sam3' ? getWorldClassesInput() : [];

    if (engine === 'sam3' && worldClasses.length === 0) {
        showToast('请先输入 SAM3 的目标类（如 base,frame,mirror,screw）');
        return;
    }
    
    // 获取区间范围
    const startIndex = Math.max(1, parseInt(startIndexInput?.value || '1'));
    const endIndex = Math.min(window.allImages.length, parseInt(endIndexInput?.value || window.allImages.length));
    
    if (startIndex > endIndex) {
        showToast('起始位置不能大于结束位置');
        return;
    }
    
    // 获取指定范围内的图片
    let imagesToAnnotate = window.allImages.slice(startIndex - 1, endIndex);
    
    // 过滤已标注的图片
    if (skipAnnotated) {
        imagesToAnnotate = imagesToAnnotate.filter(img => img.annotation_count === 0);
    }
    
    if (imagesToAnnotate.length === 0) {
        showToast('选定范围内没有需要标注的图片');
        return;
    }
    
    // 显示进度条
    const progressDiv = document.getElementById('batchAnnotateProgress');
    const progressText = document.getElementById('batchProgressText');
    const progressBar = document.getElementById('batchProgressBar');
    const resultText = document.getElementById('batchResultText');
    
    progressDiv.style.display = 'block';
    progressBar.style.width = '0%';
    resultText.innerHTML = '';
    
    // 禁用按钮
    document.getElementById('aiBatchAnnotateBtn').disabled = true;
    document.getElementById('aiAnnotateStartBtn').disabled = true;
    
    batchAnnotateRunning = true;
    batchAnnotateCancelled = false;
    
    let successCount = 0;
    let failCount = 0;
    let totalDetected = 0;
    const total = imagesToAnnotate.length;
    
    showToast(`开始批量标注 ${total} 张图片，每批 ${batchSize} 张...`);
    
    // 分批处理
    const batches = [];
    for (let i = 0; i < imagesToAnnotate.length; i += batchSize) {
        batches.push(imagesToAnnotate.slice(i, i + batchSize));
    }
    
    let processedCount = 0;
    
    for (let batchIndex = 0; batchIndex < batches.length; batchIndex++) {
        if (batchAnnotateCancelled) {
            resultText.innerHTML += `<div style="color: orange;">已取消，共处理 ${processedCount} 张图片</div>`;
            break;
        }
        
        const batch = batches[batchIndex];
        const batchImageNames = batch.map(img => img.name);
        
        progressText.textContent = `正在处理第 ${batchIndex + 1}/${batches.length} 批 (${processedCount + 1}-${processedCount + batch.length}/${total})`;
        progressBar.style.width = `${((processedCount + batch.length) / total) * 100}%`;
        
        try {
            // 使用批量API
            const endpoint = engine === 'sam3' ? '/api/ai-annotate-sam3-batch' : '/api/ai-annotate-batch';
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    image_names: batchImageNames,
                    model_name: model,
                    confidence: confidence,
                    install_path: installPath,
                    device: 'auto',
                    world_classes: worldClasses
                })
            });
            
            const data = await response.json();
            
            if (data.error) {
                failCount += batch.length;
                resultText.innerHTML += `<div style="color: red;">✗ 批次 ${batchIndex + 1} 失败: ${data.error}</div>`;
            } else if (data.results) {
                // 处理每张图片的结果
                for (const result of data.results) {
                    if (result.success) {
                        if (result.count > 0) {
                            successCount++;
                            totalDetected += result.count;
                            resultText.innerHTML += `<div style="color: green;">✓ ${result.image_name}: ${result.count} 个目标</div>`;
                        } else {
                            resultText.innerHTML += `<div style="color: gray;">○ ${result.image_name}: 无目标</div>`;
                        }
                    } else {
                        failCount++;
                        resultText.innerHTML += `<div style="color: red;">✗ ${result.image_name}: 失败</div>`;
                    }
                }
            }
            
            // 滚动到底部
            resultText.scrollTop = resultText.scrollHeight;
            
        } catch (error) {
            failCount += batch.length;
            resultText.innerHTML += `<div style="color: red;">✗ 批次 ${batchIndex + 1} 错误: ${error.message}</div>`;
        }
        
        processedCount += batch.length;
    }
    
    // 完成
    batchAnnotateRunning = false;
    progressText.textContent = `完成: 处理 ${processedCount} 张，检测到 ${totalDetected} 个目标`;
    progressBar.style.width = '100%';
    
    // 恢复按钮
    document.getElementById('aiBatchAnnotateBtn').disabled = false;
    document.getElementById('aiAnnotateStartBtn').disabled = false;
    
    // 刷新图片列表和类别
    loadImages();
    loadClasses();
    
    showToast(`批量标注完成！处理 ${processedCount} 张图片，检测到 ${totalDetected} 个目标`);
}

// 取消批量标注
function cancelBatchAnnotate() {
    if (batchAnnotateRunning) {
        batchAnnotateCancelled = true;
        showToast('正在取消批量标注...');
    }
}

function getAiRangeConfig() {
    const totalImages = window.allImages ? window.allImages.length : 0;
    const startInput = document.getElementById('batchStartIndex');
    const endInput = document.getElementById('batchEndIndex');
    let start = parseInt(startInput?.value || '1', 10);
    let end = parseInt(endInput?.value || String(totalImages || 1), 10);
    if (!Number.isFinite(start) || start < 1) start = 1;
    if (!Number.isFinite(end) || end < start) end = start;
    if (totalImages > 0) {
        start = Math.min(start, totalImages);
        end = Math.min(end, totalImages);
    }
    return { start, end, total: totalImages };
}

// ==================== 训练中心 ====================

function showTrainingCenterModal() {
    const modal = document.getElementById('trainCenterModal');
    if (!modal) return;
    modal.style.display = 'block';
    loadTrainingCenter();
}

function setupTrainingCenterEvents() {
    const refreshBtn = document.getElementById('refreshTrainCenterBtn');
    if (refreshBtn) refreshBtn.addEventListener('click', loadTrainingCenter);

    const initialBtn = document.getElementById('startInitialTrainBtn');
    if (initialBtn) initialBtn.addEventListener('click', () => startTraining('initial'));

    const incrementalBtn = document.getElementById('startIncrementalTrainBtn');
    if (incrementalBtn) incrementalBtn.addEventListener('click', () => startTraining('incremental'));

    const modal = document.getElementById('trainCenterModal');
    if (modal) {
        modal.addEventListener('click', function(e) {
            if (e.target === modal) stopTrainPolling();
        });
        const closeBtn = modal.querySelector('.close');
        if (closeBtn) closeBtn.addEventListener('click', stopTrainPolling);
    }
}

function loadTrainingCenter() {
    Promise.all([
        fetch('/api/train/readiness').then(r => r.json()),
        fetch('/api/train/jobs').then(r => r.json()),
        fetch('/api/models/registry').then(r => r.json()),
        fetch('/api/models/active').then(r => r.json())
    ])
        .then(([readiness, jobsData, modelsData, activeModel]) => {
            renderTrainReadiness(readiness);
            renderTrainJobs(jobsData.jobs || []);
            renderModelRegistry(modelsData.models || [], activeModel);
            renderActiveModel(activeModel);
            syncAiModelWithActive(activeModel);
            startTrainPollingIfNeeded(jobsData.jobs || []);
        })
        .catch(error => {
            console.error('加载训练中心失败:', error);
            showToast('加载训练中心失败: ' + error.message);
        });
}

function renderTrainReadiness(readiness) {
    const panel = document.getElementById('trainReadiness');
    if (!panel) return;
    const annotated = Number(readiness.annotated_images || 0);
    const total = Number(readiness.total_images || 0);
    const minCount = Number(readiness.min_for_initial || 100);
    const ready = !!readiness.ready_for_initial;
    panel.innerHTML = `
        <div>已标注图片：<strong>${annotated}</strong> / ${total}</div>
        <div>初代模型门槛：${minCount} 张</div>
        <div>初代训练状态：${ready ? '<span style="color:#28a745;">可开始</span>' : '<span style="color:#dc3545;">未达标</span>'}</div>
    `;
    renderCudaStatus(readiness?.cuda || {});
}

function renderCudaStatus(cuda) {
    const panel = document.getElementById('trainCudaStatus');
    if (!panel) return;

    const available = !!cuda.available;
    const deviceName = cuda.device_name || '-';
    const torchVersion = cuda.torch_version || '-';
    const deviceCount = Number(cuda.device_count || 0);
    const error = cuda.error ? `（${escapeHtml(cuda.error)}）` : '';

    panel.innerHTML = available
        ? `CUDA状态：<span style="color:#28a745;font-weight:600;">可用</span> | GPU: <strong>${escapeHtml(deviceName)}</strong> | 数量: ${deviceCount} | Torch: ${escapeHtml(torchVersion)}`
        : `CUDA状态：<span style="color:#dc3545;font-weight:600;">不可用</span> | Torch: ${escapeHtml(torchVersion)} ${error}`;
}

function renderActiveModel(activeModel) {
    const panel = document.getElementById('activeModelInfo');
    if (!panel) return;
    if (!activeModel || !activeModel.model_name) {
        panel.innerHTML = '当前生产模型：<strong>无</strong>';
        return;
    }
    const updated = activeModel.updated_at ? `（${activeModel.updated_at}）` : '';
    panel.innerHTML = `当前生产模型：<strong>${escapeHtml(activeModel.model_name)}</strong> ${escapeHtml(updated)}`;
}

function renderTrainJobs(jobs) {
    const panel = document.getElementById('trainJobsPanel');
    if (!panel) return;
    if (!jobs.length) {
        panel.innerHTML = '<div class="timeline-empty">暂无训练任务</div>';
        return;
    }
    const rows = jobs.slice(0, 15).map((job, idx) => {
        const statusMap = {
            queued: '排队中',
            running: '训练中',
            completed: '完成',
            failed: '失败'
        };
        const statusText = statusMap[job.status] || job.status || 'unknown';
        const percent = Math.max(0, Math.min(100, Number(job.progress || 0)));
        return `
            <tr>
                <td>${idx + 1}</td>
                <td>${escapeHtml(job.mode || '')}</td>
                <td>${escapeHtml(statusText)}</td>
                <td>${percent}%</td>
                <td>${escapeHtml(job.message || '')}</td>
                <td>${escapeHtml(job.version || '-')}</td>
            </tr>
        `;
    }).join('');
    panel.innerHTML = `
        <table class="train-table">
            <thead>
                <tr><th>#</th><th>模式</th><th>状态</th><th>进度</th><th>信息</th><th>产出版本</th></tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;
}

function renderModelRegistry(models, activeModel) {
    const panel = document.getElementById('modelRegistryPanel');
    if (!panel) return;
    if (!models.length) {
        panel.innerHTML = '<div class="timeline-empty">暂无训练模型</div>';
        return;
    }
    const activeId = activeModel?.model_id || '';
    const rows = models.slice(0, 20).map((model, idx) => {
        const isActive = activeId && activeId === model.id;
        const metrics = model.metrics || {};
        const map50 = Number(metrics['metrics/mAP50(B)'] || 0);
        const precision = Number(metrics['metrics/precision(B)'] || 0);
        const recall = Number(metrics['metrics/recall(B)'] || 0);
        const metricText = (map50 > 0 || precision > 0 || recall > 0)
            ? `P:${precision.toFixed(3)} R:${recall.toFixed(3)} mAP50:${map50.toFixed(3)}`
            : '-';
        return `
            <tr>
                <td>${idx + 1}</td>
                <td>${escapeHtml(model.version || '')}</td>
                <td>${escapeHtml(model.name || '')}</td>
                <td>${escapeHtml(model.mode || '')}</td>
                <td>${escapeHtml(metricText)}</td>
                <td>
                    ${isActive ? '<span style="color:#28a745;font-weight:600;">生产中</span>' : `<button class="btn btn-small btn-primary" onclick="activateModel('${escapeHtml(model.id || '')}')">设为生产</button>`}
                </td>
            </tr>
        `;
    }).join('');
    panel.innerHTML = `
        <table class="train-table">
            <thead>
                <tr><th>#</th><th>版本</th><th>文件</th><th>训练模式</th><th>指标</th><th>操作</th></tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;
}

function startTraining(mode) {
    if (mode === 'initial') {
        const annotated = (window.allImages || []).filter(img => Number(img.annotation_count || 0) > 0).length;
        if (annotated < COLD_START_MIN_ANNOTATED) {
            showToast(`请至少标注 ${COLD_START_MIN_ANNOTATED} 张图片后再开始训练（当前 ${annotated} 张）`);
            return;
        }
    }

    const epochs = Number(document.getElementById('trainEpochs')?.value || 30);
    const imgsz = Number(document.getElementById('trainImgsz')?.value || 640);
    const batch = Number(document.getElementById('trainBatch')?.value || 8);

    fetch('/api/train/start', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            mode,
            epochs,
            imgsz,
            batch,
            device: 'auto'
        })
    })
        .then(async response => {
            const data = await response.json();
            if (!response.ok || data.error) {
                throw new Error(data.error || '启动训练失败');
            }
            showToast(`训练任务已启动：${data.job?.id || ''}`);
            loadTrainingCenter();
        })
        .catch(error => {
            console.error('启动训练失败:', error);
            showToast('启动训练失败: ' + error.message);
        });
}

function activateModel(modelId) {
    fetch(`/api/models/${encodeURIComponent(modelId)}/activate`, {
        method: 'POST'
    })
        .then(async response => {
            const data = await response.json();
            if (!response.ok || data.error) {
                throw new Error(data.error || '切换模型失败');
            }
            showToast('生产模型已切换');
            loadTrainingCenter();
        })
        .catch(error => {
            console.error('切换生产模型失败:', error);
            showToast('切换生产模型失败: ' + error.message);
        });
}

function startTrainPollingIfNeeded(jobs) {
    const hasRunning = (jobs || []).some(job => job.status === 'queued' || job.status === 'running');
    if (!hasRunning) {
        stopTrainPolling();
        return;
    }
    if (trainCenterPolling) return;
    const tick = () => {
        fetch('/api/train/jobs')
            .then(response => response.json())
            .then(data => {
                const allJobs = data.jobs || [];
                renderTrainJobs(allJobs);
                const stillRunning = allJobs.some(job => job.status === 'queued' || job.status === 'running');
                if (stillRunning) {
                    trainCenterPolling = setTimeout(tick, 3000);
                } else {
                    trainCenterPolling = null;
                    loadTrainingCenter();
                }
            })
            .catch(() => {
                trainCenterPolling = setTimeout(tick, 5000);
            });
    };
    trainCenterPolling = setTimeout(tick, 2000);
}

function stopTrainPolling() {
    if (trainCenterPolling) {
        clearTimeout(trainCenterPolling);
        trainCenterPolling = null;
    }
}

function syncAiModelWithActive(activeModel) {
    const activeModelName =
        activeModel?.model_name ||
        ((activeModel?.model_path || '').split(/[\\/]/).pop() || '');
    if (!activeModelName) return;
    aiAnnotateModel = activeModelName;
    const select = document.getElementById('aiModelSelect');
    if (select) {
        const option = Array.from(select.options).find(x => x.value === activeModelName);
        if (option) select.value = activeModelName;
    }
}


// ==================== SOP timeline annotation ====================

function showTimelineModal() {
    const modal = document.getElementById('timelineModal');
    if (!modal) return;
    modal.style.display = 'block';
    loadSopScenario();
    loadTimelineVideos();
}

function setupTimelineEvents() {
    const importBtn = document.getElementById('importScenarioBtn');
    if (importBtn) importBtn.addEventListener('click', importSopScenario);

    const uploadBtn = document.getElementById('uploadTimelineVideoBtn');
    if (uploadBtn) uploadBtn.addEventListener('click', uploadTimelineVideo);

    const refreshBtn = document.getElementById('refreshTimelineVideosBtn');
    if (refreshBtn) refreshBtn.addEventListener('click', loadTimelineVideos);

    const loadBtn = document.getElementById('loadTimelineVideoBtn');
    if (loadBtn) loadBtn.addEventListener('click', loadSelectedTimelineVideo);

    const markStartBtn = document.getElementById('markStartBtn');
    if (markStartBtn) markStartBtn.addEventListener('click', () => markTimelineTime('segmentStartSec'));

    const markEndBtn = document.getElementById('markEndBtn');
    if (markEndBtn) markEndBtn.addEventListener('click', () => markTimelineTime('segmentEndSec'));

    const addBtn = document.getElementById('addSegmentBtn');
    if (addBtn) addBtn.addEventListener('click', addTimelineSegment);

    const saveBtn = document.getElementById('saveTimelineBtn');
    if (saveBtn) saveBtn.addEventListener('click', saveTimelineSegments);

    const exportBtn = document.getElementById('exportTimelineBtn');
    if (exportBtn) exportBtn.addEventListener('click', exportTimelineCsv);

    const stepSelect = document.getElementById('timelineStepSelect');
    if (stepSelect) stepSelect.addEventListener('change', fillTimelineFieldsFromStep);

    const player = document.getElementById('timelineVideoPlayer');
    if (player) {
        player.addEventListener('timeupdate', function() {
            const el = document.getElementById('timelineCurrentTime');
            if (el) el.textContent = (player.currentTime || 0).toFixed(3);
        });
    }
}

function loadSopScenario() {
    fetch('/api/scenario')
        .then(response => response.json())
        .then(data => {
            sopScenario = data || {steps: [], object_classes: [], action_labels: []};
            renderSopScenario();
        })
        .catch(error => {
            console.error('Failed to load scenario:', error);
        });
}

function importSopScenario() {
    const input = document.getElementById('scenarioPathInput');
    const scenarioPath = input ? input.value.trim() : '';
    if (!scenarioPath) {
        showToast('Please input scenario path');
        return;
    }
    fetch('/api/scenario/import', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({scenario_path: scenarioPath, replace_classes: true})
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) throw new Error(data.error);
        sopScenario = data.scenario;
        renderSopScenario();
        loadClasses();
        showToast(`Scenario imported: ${sopScenario.name || sopScenario.scenario_id}`);
    })
    .catch(error => {
        console.error('Failed to import scenario:', error);
        showToast('Import failed: ' + error.message);
    });
}

function renderSopScenario() {
    const summary = document.getElementById('scenarioSummary');
    const stepSelect = document.getElementById('timelineStepSelect');
    const objectClasses = sopScenario.object_classes || [];
    const steps = sopScenario.steps || [];
    if (summary) {
        if (sopScenario.scenario_id || steps.length > 0) {
            summary.innerHTML = `Scenario: <strong>${escapeHtml(sopScenario.name || sopScenario.scenario_id)}</strong>; steps: ${steps.length}; objects: ${objectClasses.length}`;
        } else {
            summary.textContent = 'No scenario imported yet.';
        }
    }
    if (stepSelect) {
        stepSelect.innerHTML = '<option value="">-- Select Step --</option>';
        steps.forEach(step => {
            const option = document.createElement('option');
            option.value = step.id || '';
            option.textContent = `${step.id || ''} ${step.name || ''}`.trim();
            option.dataset.actionLabel = step.action_label || '';
            option.dataset.targetIds = (step.target_ids || []).join(',');
            option.dataset.eventType = step.event_type || `${step.id || step.action_label || 'step'}_done`;
            stepSelect.appendChild(option);
        });
    }
}

function fillTimelineFieldsFromStep() {
    const stepSelect = document.getElementById('timelineStepSelect');
    if (!stepSelect || !stepSelect.selectedOptions.length) return;
    const option = stepSelect.selectedOptions[0];
    const actionInput = document.getElementById('timelineActionLabel');
    const targetInput = document.getElementById('timelineTargetId');
    const eventInput = document.getElementById('timelineEventType');
    if (actionInput && option.dataset.actionLabel) actionInput.value = option.dataset.actionLabel;
    if (targetInput && option.dataset.targetIds) targetInput.value = option.dataset.targetIds.split(',')[0] || '';
    if (eventInput && option.dataset.eventType) eventInput.value = option.dataset.eventType;
}

function uploadTimelineVideo() {
    const input = document.getElementById('timelineVideoInput');
    if (!input || !input.files || input.files.length === 0) {
        showToast('Please select a workflow video first');
        return;
    }
    const formData = new FormData();
    formData.append('video', input.files[0], input.files[0].name);
    fetch('/api/upload/timeline-video', {method: 'POST', body: formData})
        .then(response => response.json())
        .then(data => {
            if (data.error) throw new Error(data.error);
            currentTimelineVideo = data.video_name;
            showToast(`Video uploaded: ${data.video_name}`);
            return loadTimelineVideos(data.video_name);
        })
        .then(() => loadSelectedTimelineVideo())
        .catch(error => {
            console.error('Failed to upload timeline video:', error);
            showToast('Upload failed: ' + error.message);
        });
}

function loadTimelineVideos(preferredVideo) {
    return fetch('/api/videos')
        .then(response => response.json())
        .then(data => {
            const select = document.getElementById('timelineVideoSelect');
            if (!select) return;
            const previous = preferredVideo || currentTimelineVideo || select.value;
            select.innerHTML = '<option value="">-- Select Video --</option>';
            (data.videos || []).forEach(video => {
                const option = document.createElement('option');
                option.value = video.name;
                option.textContent = video.name;
                select.appendChild(option);
            });
            if (previous) select.value = previous;
        })
        .catch(error => {
            console.error('Failed to load videos:', error);
            showToast('Load videos failed: ' + error.message);
        });
}

function loadSelectedTimelineVideo() {
    const select = document.getElementById('timelineVideoSelect');
    const videoName = select ? select.value : '';
    if (!videoName) {
        showToast('Please select a video first');
        return;
    }
    currentTimelineVideo = videoName;
    const player = document.getElementById('timelineVideoPlayer');
    if (player) {
        player.src = `/api/video/${encodeURIComponent(videoName)}`;
        player.load();
    }
    fetch(`/api/timelines/${encodeURIComponent(videoName)}`)
        .then(response => response.json())
        .then(data => {
            timelineSegments = Array.isArray(data) ? data : [];
            renderTimelineSegments();
            showToast(`Loaded ${timelineSegments.length} segments for ${videoName}`);
        })
        .catch(error => {
            console.error('Failed to load timeline:', error);
            timelineSegments = [];
            renderTimelineSegments();
        });
}

function markTimelineTime(inputId) {
    const player = document.getElementById('timelineVideoPlayer');
    const input = document.getElementById(inputId);
    if (!player || !input) return;
    input.value = (player.currentTime || 0).toFixed(3);
}

function addTimelineSegment() {
    if (!currentTimelineVideo) {
        showToast('Please load a video first');
        return;
    }
    const start = parseFloat(document.getElementById('segmentStartSec')?.value || 'NaN');
    const end = parseFloat(document.getElementById('segmentEndSec')?.value || 'NaN');
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
        showToast('Invalid start/end time');
        return;
    }
    const stepSelect = document.getElementById('timelineStepSelect');
    const segment = {
        id: `seg_${Date.now()}`,
        video_name: currentTimelineVideo,
        start_sec: Number(start.toFixed(3)),
        end_sec: Number(end.toFixed(3)),
        step_id: stepSelect ? stepSelect.value : '',
        action_label: document.getElementById('timelineActionLabel')?.value.trim() || '',
        target_id: document.getElementById('timelineTargetId')?.value.trim() || '',
        part_id: '',
        event_type: document.getElementById('timelineEventType')?.value.trim() || '',
        is_complete: parseInt(document.getElementById('timelineIsComplete')?.value || '1'),
        error_type: document.getElementById('timelineErrorType')?.value.trim() || '',
        remark: document.getElementById('timelineRemark')?.value.trim() || ''
    };
    if (!segment.step_id || !segment.action_label) {
        showToast('Step and action label are required');
        return;
    }
    timelineSegments.push(segment);
    timelineSegments.sort((a, b) => (a.start_sec - b.start_sec) || (a.end_sec - b.end_sec));
    renderTimelineSegments();
    document.getElementById('segmentStartSec').value = '';
    document.getElementById('segmentEndSec').value = '';
}

function renderTimelineSegments() {
    const container = document.getElementById('timelineSegmentList');
    if (!container) return;
    if (!timelineSegments.length) {
        container.innerHTML = '<div class="timeline-empty">No segment yet. Mark start/end and add segments.</div>';
        return;
    }
    const rows = timelineSegments.map((seg, index) => `
        <tr>
            <td>${index + 1}</td>
            <td>${Number(seg.start_sec).toFixed(3)}-${Number(seg.end_sec).toFixed(3)}</td>
            <td>${escapeHtml(seg.step_id || '')}</td>
            <td>${escapeHtml(seg.action_label || '')}</td>
            <td>${escapeHtml(seg.target_id || '')}</td>
            <td>${seg.is_complete ? 'done' : 'not-done'}</td>
            <td>
                <button class="btn btn-small btn-secondary" onclick="jumpToTimelineSegment(${index})">Jump</button>
                <button class="btn btn-small btn-danger" onclick="deleteTimelineSegment(${index})">Delete</button>
            </td>
        </tr>`).join('');
    container.innerHTML = `
        <table class="timeline-segment-table">
            <thead><tr><th>#</th><th>Time</th><th>Step</th><th>Action</th><th>Target</th><th>Status</th><th>Ops</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
}

function jumpToTimelineSegment(index) {
    const seg = timelineSegments[index];
    const player = document.getElementById('timelineVideoPlayer');
    if (seg && player) {
        player.currentTime = Number(seg.start_sec) || 0;
        player.play();
    }
}

function deleteTimelineSegment(index) {
    timelineSegments.splice(index, 1);
    renderTimelineSegments();
}

function saveTimelineSegments() {
    if (!currentTimelineVideo) {
        showToast('Please load a video first');
        return;
    }
    fetch(`/api/timelines/${encodeURIComponent(currentTimelineVideo)}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({segments: timelineSegments})
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) throw new Error(data.error);
        timelineSegments = data.segments || timelineSegments;
        renderTimelineSegments();
        showToast(`Timeline saved: ${data.count} segments`);
    })
    .catch(error => {
        console.error('Failed to save timeline:', error);
        showToast('Save failed: ' + error.message);
    });
}

function exportTimelineCsv() {
    fetch('/api/export-timeline')
        .then(response => response.blob())
        .then(blob => {
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'timeline.csv';
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
        })
        .catch(error => {
            console.error('Failed to export timeline:', error);
            showToast('Export failed: ' + error.message);
        });
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[ch]));
}
