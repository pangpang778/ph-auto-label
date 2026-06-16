/* 视频AI对比测试页交互 — 全帧离线推理 + SSE 进度 + 双视频同步 + 刷新恢复 */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const JOB_KEY = 'vt_current_job';   // {job_id, ts}
    const VIDEO_KEY = 'vt_current_video';

    const els = {
        videoSelect: $('videoSelect'),
        refreshVideos: $('refreshVideosBtn'),
        uploadBtn: $('uploadBtn'),
        videoFile: $('videoFile'),
        uploadMsg: $('uploadMsg'),
        yoloPanel: $('yoloPanel'),
        sam3Panel: $('sam3Panel'),
        yoloModel: $('yoloModel'),
        sam3Classes: $('sam3Classes'),
        confRange: $('confRange'),
        confVal: $('confVal'),
        startBtn: $('startBtn'),
        statusArea: $('statusArea'),
        progressBar: $('progressBar'),
        statusText: $('statusText'),
        origVideo: $('origVideo'),
        aiVideo: $('aiVideo'),
        aiBadge: $('aiBadge'),
        syncToggle: $('syncToggle'),
        syncInfo: $('syncInfo'),
    };

    let currentJobId = null;
    let eventSource = null;

    // ---------- 任务持久化 ----------
    function saveJob(jobId) {
        try { localStorage.setItem(JOB_KEY, JSON.stringify({ job_id: jobId, ts: Date.now() })); } catch {}
    }
    function loadJob() {
        try { return JSON.parse(localStorage.getItem(JOB_KEY) || 'null'); } catch { return null; }
    }
    function clearJob() { try { localStorage.removeItem(JOB_KEY); } catch {} }
    function saveVideo(name) { try { localStorage.setItem(VIDEO_KEY, name); } catch {} }

    // ---------- 初始化 ----------
    async function loadVideos() {
        try {
            const r = await fetch('/api/video-test/videos');
            const d = await r.json();
            els.videoSelect.innerHTML = '';
            (d.videos || []).forEach(v => {
                const opt = document.createElement('option');
                opt.value = v.name;
                opt.textContent = `${v.name} (${v.source === 'default' ? '默认' : '上传'}, ${(v.size / 1048576).toFixed(1)}MB)`;
                els.videoSelect.appendChild(opt);
            });
            if (!d.videos || !d.videos.length) {
                els.videoSelect.innerHTML = '<option value="">无可用视频，请上传</option>';
            }
        } catch (e) {
            els.videoSelect.innerHTML = '<option>加载失败</option>';
        }
    }

    async function loadYoloModels() {
        try {
            const r = await fetch('/api/video-test/yolo-models');
            const d = await r.json();
            els.yoloModel.innerHTML = '';
            (d.models || []).forEach(m => {
                const opt = document.createElement('option');
                opt.value = m.value;
                opt.textContent = m.name;
                els.yoloModel.appendChild(opt);
            });
            if (d.active) {
                const match = (d.models || []).find(m => m.value === d.active);
                if (match) els.yoloModel.value = match.value;
            }
        } catch (e) {
            els.yoloModel.innerHTML = '<option value="yolo11n.pt">yolo11n.pt</option>';
        }
    }

    function selectVideo(name) {
        if (!name) return;
        els.origVideo.src = `/api/video-test/video/${encodeURIComponent(name)}`;
        els.origVideo.load();
        saveVideo(name);
    }

    els.videoSelect.addEventListener('change', () => {
        const opt = els.videoSelect.options[els.videoSelect.selectedIndex];
        if (opt && opt.value) {
            selectVideo(opt.value);
            els.aiVideo.removeAttribute('src');
            els.aiVideo.load();
            els.aiBadge.textContent = '待生成';
            els.aiBadge.classList.remove('ready');
        }
    });
    els.refreshVideos.addEventListener('click', loadVideos);

    // ---------- 上传 ----------
    els.uploadBtn.addEventListener('click', () => els.videoFile.click());
    els.videoFile.addEventListener('change', async () => {
        const file = els.videoFile.files[0];
        if (!file) return;
        els.uploadMsg.textContent = '上传中...';
        const fd = new FormData();
        fd.append('video', file);
        try {
            const r = await fetch('/api/video-test/upload', { method: 'POST', body: fd });
            const d = await r.json();
            if (!r.ok) throw new Error(d.error || '上传失败');
            els.uploadMsg.textContent = `✓ ${d.name} 上传成功`;
            await loadVideos();
            els.videoSelect.value = d.name;
            els.videoSelect.dispatchEvent(new Event('change'));
        } catch (e) {
            els.uploadMsg.textContent = '✗ ' + e.message;
        } finally {
            els.videoFile.value = '';
        }
    });

    // ---------- 引擎/置信度 ----------
    function updateEnginePanel() {
        const engine = document.querySelector('input[name="engine"]:checked').value;
        els.yoloPanel.style.display = engine === 'yolo' ? '' : 'none';
        els.sam3Panel.style.display = engine === 'sam3' ? '' : 'none';
    }
    document.querySelectorAll('input[name="engine"]').forEach(r => r.addEventListener('change', updateEnginePanel));
    els.confRange.addEventListener('input', () => { els.confVal.textContent = els.confRange.value; });

    // ---------- 开始全帧推理 ----------
    els.startBtn.addEventListener('click', startInference);

    function startInference() {
        const engine = document.querySelector('input[name="engine"]:checked').value;
        const videoName = els.videoSelect.value;
        if (!videoName) { alert('请先选择视频'); return; }

        const body = { video_name: videoName, engine, confidence: parseFloat(els.confRange.value) };
        if (engine === 'yolo') {
            body.model = els.yoloModel.value || 'yolo11n.pt';
        } else {
            body.classes = els.sam3Classes.value;
            if (!body.classes.trim()) { alert('请填写 SAM3 目标类别'); return; }
        }

        els.startBtn.disabled = true;
        els.statusArea.style.display = '';
        els.statusArea.classList.remove('error', 'done');
        setProgress(0, '提交任务...');
        els.aiBadge.textContent = '推理中';
        els.aiBadge.classList.remove('ready');
        els.aiVideo.removeAttribute('src');

        fetch('/api/video-test/start', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        }).then(r => r.json()).then(d => {
            if (d.error) throw new Error(d.error);
            currentJobId = d.job_id;
            saveJob(d.job_id);          // 持久化，刷新可恢复
            openStream(d.job_id);
        }).catch(e => {
            setProgress(0, '✗ ' + e.message, true);
            els.startBtn.disabled = false;
            els.aiBadge.textContent = '失败';
        });
    }

    // ---------- SSE 进度 ----------
    function openStream(jobId) {
        if (eventSource) eventSource.close();
        eventSource = new EventSource(`/api/video-test/stream/${jobId}`);
        eventSource.onmessage = (ev) => {
            let d; try { d = JSON.parse(ev.data); } catch { return; }
            handleProgress(d);
        };
        eventSource.onerror = () => { if (currentJobId) checkJobOnce(currentJobId); };
    }

    function handleProgress(d) {
        if (d.progress !== undefined) setProgress(d.progress, d.message || '', d.status === 'failed');
        if (d.status === 'completed') {
            closeStream();
            els.statusArea.classList.add('done');
            setProgress(100, d.message || '完成');
            els.startBtn.disabled = false;
            if (d.ai_video_url) {
                els.aiVideo.src = d.ai_video_url;
                els.aiVideo.load();
                els.aiBadge.textContent = '已生成';
                els.aiBadge.classList.add('ready');
            }
        } else if (d.status === 'failed') {
            closeStream();
            els.statusArea.classList.add('error');
            els.startBtn.disabled = false;
            els.aiBadge.textContent = '失败';
            setProgress(d.progress || 0, '✗ ' + (d.error || d.message || '失败'), true);
        }
    }

    async function checkJobOnce(jobId) {
        try {
            const r = await fetch(`/api/video-test/job/${jobId}`);
            if (r.ok) handleProgress(await r.json());
        } catch {}
    }
    function closeStream() { if (eventSource) { eventSource.close(); eventSource = null; } }
    function setProgress(pct, msg, isError) {
        els.progressBar.style.width = Math.max(0, Math.min(100, pct)) + '%';
        if (msg) els.statusText.textContent = msg;
        if (isError) els.statusArea.classList.add('error');
    }

    // ---------- 刷新恢复 ----------
    async function resumeJob() {
        const saved = loadJob();
        if (!saved || !saved.job_id) return;
        let d;
        try {
            const r = await fetch(`/api/video-test/job/${saved.job_id}`);
            if (!r.ok) { clearJob(); return; }
            d = await r.json();
        } catch { clearJob(); return; }
        if (!d || d.error || !d.status) { clearJob(); return; }

        currentJobId = saved.job_id;
        els.statusArea.style.display = '';
        if (d.status === 'running' || d.status === 'queued') {
            els.startBtn.disabled = true;
            els.aiBadge.textContent = '推理中';
            setProgress(d.progress || 0, d.message || '恢复进度...');
            openStream(saved.job_id);   // 重连 SSE 继续跟进
        } else if (d.status === 'completed') {
            els.statusArea.classList.add('done');
            setProgress(100, d.message || '已完成');
            if (d.ai_video_url) {
                els.aiVideo.src = d.ai_video_url;
                els.aiVideo.load();
                els.aiBadge.textContent = '已生成';
                els.aiBadge.classList.add('ready');
            }
        } else if (d.status === 'failed') {
            els.statusArea.classList.add('error');
            els.aiBadge.textContent = '失败';
            setProgress(d.progress || 0, '✗ ' + (d.error || '失败'), true);
        }
    }

    // ---------- 双视频同步联动 ----------
    let syncing = false;
    function syncFrom(src) {
        if (!els.syncToggle.checked || syncing) return;
        syncing = true;
        const target = src === els.origVideo ? els.aiVideo : els.origVideo;
        if (target.src && Math.abs(target.currentTime - src.currentTime) > 0.25) target.currentTime = src.currentTime;
        syncing = false;
    }
    function syncPlayState(src, playing) {
        if (!els.syncToggle.checked || syncing) return;
        syncing = true;
        const target = src === els.origVideo ? els.aiVideo : els.origVideo;
        if (playing) { if (target.src) target.play().catch(() => {}); } else target.pause();
        syncing = false;
    }
    els.origVideo.addEventListener('play', () => syncPlayState(els.origVideo, true));
    els.origVideo.addEventListener('pause', () => syncPlayState(els.origVideo, false));
    els.origVideo.addEventListener('seeked', () => syncFrom(els.origVideo));
    els.aiVideo.addEventListener('play', () => syncPlayState(els.aiVideo, true));
    els.aiVideo.addEventListener('pause', () => syncPlayState(els.aiVideo, false));
    els.aiVideo.addEventListener('seeked', () => syncFrom(els.aiVideo));
    els.syncToggle.addEventListener('change', () => {
        els.syncInfo.textContent = els.syncToggle.checked ? '已开启联动' : '已关闭联动';
    });

    // ---------- 启动 ----------
    (async function init() {
        updateEnginePanel();
        await loadVideos();
        await loadYoloModels();
        // 恢复上次选的视频（否则选第一个）
        const savedVideo = localStorage.getItem(VIDEO_KEY);
        const opts = [...els.videoSelect.options].map(o => o.value).filter(Boolean);
        const target = (savedVideo && opts.includes(savedVideo)) ? savedVideo : opts[0];
        if (target) {
            els.videoSelect.value = target;
            selectVideo(target);
        }
        // 恢复正在跑/已完成的任务（刷新不丢）
        await resumeJob();
    })();
})();
