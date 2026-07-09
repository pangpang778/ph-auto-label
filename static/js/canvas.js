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
function drawCrosshair(e) {
    // 不再直接在画布上绘制十字线，避免重影问题
    // 重绘画布时会清除所有临时绘制
    return;
}

// 切换手风琴折叠状态
