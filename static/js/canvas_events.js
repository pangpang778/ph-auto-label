// 画布鼠标事件 / 工具切换：switchTool / handleMouseDown / handleMouseMove / handleMouseUp / handleDoubleClick / handleMouseLeave
// 依赖全局符号（state.js 声明）：currentImage / currentTool / isDrawing / isPolygonDrawing / startPoint / currentPoint / polygonPoints / imageCache / isResizing / isMoving / resizeHandle / lastMousePos / selectedAnnotationId / currentAnnotations
// 跨文件调用（classic-script 全局）：redrawCanvas / drawCrosshair（canvas.js）、checkResizeHandleClick / checkAnnotationClick / resizeAnnotation / moveAnnotation（canvas_handles.js）；另调用全局 saveAnnotationsSilent / updateAnnotationList / updateAnnotationListDebounced / getSelectedClass（其它模块）

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
