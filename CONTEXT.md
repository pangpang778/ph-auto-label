# CONTEXT — 领域词汇表

## 任务模式 (Task Mode)
视频测试页的第二层选择：普通检测 (detect) 或 跟踪+深度 (depth_track)。引擎 = 检测器（YOLO/SAM3），任务模式 = 检测器之上的复合能力。

## 深度教师 (Depth Teacher)
提供伪深度标签的预训练 metric 深度模型（如 ZoeDepth / Metric Depth Anything）。类比：SAM3 之于检测标注。

## 伪深度标签 (Pseudo Depth Labels)
深度教师对平台图片批量推理得到的整帧深度图（.npy），作为学生模型的训练目标。非人工真值。

## 学生深度模型 (Student Depth Model)
用伪深度标签蒸馏训练出的自研轻量模型，输出整帧 H×W 深度图（米）。注册进模型列表后可被视频测试页选用。

## 蒸馏 (Distillation)
教师打标 → 学生学习的训练范式，本平台深度能力的核心路线。

## 源视频时基 (Source Timeline)
原视频按其真实帧顺序、时间戳、帧率/可变帧率和音频时长构成的播放基准。AI 视频必须与这个基准对齐。

## VLM 观测帧 (VLM Observation Frame)
送入 VLM 做语义检测的源视频帧。观测帧是检测更新点，不等同于 AI 视频中的每一帧；观测间隔由任务参数决定。

## AI 渲染帧 (AI Render Frame)
输出 AI 视频中的源时间线帧。它可以使用最近一次有效的 VLM 结果或两个观测结果之间的传播结果，但不能改变源视频的播放时基。

## 推理结果顺序 (Inference Result Ordering)
推理结果归属于源视频的时间位置，而不是请求完成的先后。结果以源时间戳和帧序号确定顺序，迟到结果不能改变已经输出的时间位置。

## VLM 抽帧频率 (VLM Sampling Rate)
控制 VLM 观测帧产生频率的任务参数。它影响检测更新频率，不改变 AI 视频的源视频时基。

## 时序结果传播 (Temporal Result Propagation)
在相邻 VLM 观测之间，为 AI 渲染帧提供连续目标状态的过程。传播结果必须有明确的有效期限，不能把过期目标永久保留。

## 推理积压策略 (Inference Backpressure)
当 VLM 处理速度低于观测帧产生速度时，对未完成请求的处理规则。离线任务优先保证结果完整，实时预览优先保证当前画面新鲜。

## 通用时序管线 (Engine-Agnostic Temporal Pipeline)
YOLO、SAM3、VLM 和深度跟踪共享同一条源视频时序链路。引擎只决定如何产生观测结果，不改变源视频时基、结果归属和输出顺序。

## 引擎适配策略 (Engine Policy)
各推理引擎可以拥有不同的默认观测频率、并发能力和结果传播方式，但这些差异必须服从统一的源视频时序契约。
