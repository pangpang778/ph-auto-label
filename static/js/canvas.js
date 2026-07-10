// 画布绘制原语：redraw / drawImageAndAnnotations / drawAnnotations / drawAnnotation / drawControlPoints / drawCrosshair
// 依赖全局符号（state.js 声明）：currentImage / currentAnnotations / currentTool / imageCache / isDrawing / isPolygonDrawing / startPoint / currentPoint / polygonPoints / selectedAnnotationId / classes / drawResizeHandles（定义于 canvas_handles.js）

// 获取选中的类别
function redrawCanvas() {
    const canvas = document.getElementById('imageCanvas');
    const ctx = canvas.getContext('2d');
    const container = document.getElementById('imageCanvasContainer');

    // ponytail: 处理 devicePixelRatio，避免高 DPI 屏幕模糊；CSS 像素尺寸不变，鼠标坐标映射（getBoundingClientRect, CSS 像素）不受影响
    const w = container.clientWidth;
    const h = container.clientHeight;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // 清空画布（CSS 像素坐标系，setTransform 后 clearRect 用 CSS 尺寸）
    ctx.clearRect(0, 0, w, h);

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

// ponytail: 窗口 resize 时重绘画布；只注册一次（文件顶层 IIFE 守卫）
if (!window.__canvasResizeBound) {
    window.__canvasResizeBound = true;
    window.addEventListener('resize', redrawCanvas);
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

// 加载类别
function drawCrosshair(e) {
    // 不再直接在画布上绘制十字线，避免重影问题
    // 重绘画布时会清除所有临时绘制
    return;
}

// 切换手风琴折叠状态
