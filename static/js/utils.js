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
function showToast(message, level) {
    const toast = document.getElementById('toast');
    if (!toast) return; // ponytail: toast 元素未渲染时静默跳过，避免 null 报错

    // ponytail: level 可为 'error'|'warning'|'info'，缺省按 info 处理（保持单参数调用兼容）
    const validLevels = ['error', 'warning', 'info'];
    const severity = validLevels.includes(level) ? level : 'info';

    // 清理旧的级别 class，再挂上当前级别
    validLevels.forEach(l => toast.classList.remove(`toast-${l}`));
    toast.classList.add(`toast-${severity}`);

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
function fetchOptionalJson(url, options = {}) {
    return fetch(url, options)
        .then(async response => {
            if (response.status === 404) return { unavailable: true };
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                return { unavailable: true, error: data.error || response.statusText };
            }
            return data;
        })
        .catch(error => ({ unavailable: true, error: error.message }));
}

function hasArtifact(record, kind) {
    if (!record) return false;
    if (kind === 'model') return !!(record.artifact_path || record.weights_path || record.path);
    if (kind === 'results_csv') return !!record.results_csv;
    if (kind === 'results_png') return !!record.results_png;
    if (kind === 'log') return !!(record.log_path || record.log_tail);
    return false;
}

function formatSplitCounts(counts) {
    if (!counts) return '-';
    return `T${Number(counts.train || 0)}/V${Number(counts.val || 0)}/Te${Number(counts.test || 0)}`;
}

function formatMetricValue(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric.toFixed(4) : (value ?? '-');
}

function chartColor(index, alpha = 1) {
    const colors = ['54,163,255', '52,211,153', '251,191,36', '248,113,113', '167,139,250', '45,212,191'];
    return `rgba(${colors[index % colors.length]}, ${alpha})`;
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[ch]));
}
