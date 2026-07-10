// 画布调整大小控制点 / 移动 / 点击检测：checkResizeHandleClick / checkAnnotationClick / resizeAnnotation / moveAnnotation / drawResizeHandles
// 依赖全局符号（state.js 声明）：currentAnnotations / selectedAnnotationId
// 被 canvas.js（drawAnnotation）与 canvas_events.js（鼠标处理）调用

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
