
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
let updateAnnotationListDebounced;  // 防抖后的标注列表更新函数  // init moved to initializeApp()

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
let trainSplitState = null;
let trainMetricsChart = null;

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
