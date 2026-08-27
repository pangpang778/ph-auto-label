/* 视频AI四分屏测试页交互 — 原声保留 + 四路同步播放 */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const JOB_KEY = 'vt_current_job';
    const VIDEO_KEY = 'vt_current_video';

    const els = {
        videoSelect: $('videoSelect'),
        refreshVideos: $('refreshVideosBtn'),
        uploadBtn: $('uploadBtn'),
        videoFile: $('videoFile'),
        uploadMsg: $('uploadMsg'),
        yoloPanel: $('yoloPanel'),
        sam3Panel: $('sam3Panel'),
        vlmPanel: $('vlmPanel'),
        vlmModel: $('vlmModel'),
        depthPanel: $('depthPanel'),
        depthModelSelect: $('depthModelSelect'),
        showMetersToggle: $('showMetersToggle'),
        yoloModel: $('yoloModel'),
        sam3Classes: $('sam3Classes'),
        confRange: $('confRange'),
        confVal: $('confVal'),
        startBtn: $('startBtn'),
        statusArea: $('statusArea'),
        progressBar: $('progressBar'),
        statusText: $('statusText'),
        origVideo: $('origVideo'),
        origVideoB: $('origVideoB'),
        aiVideo: $('aiVideo'),
        aiVideoB: $('aiVideoB'),
        aiBadge: $('aiBadge'),
        syncToggle: $('syncToggle'),
        syncInfo: $('syncInfo'),
        volumeRange: $('volumeRange'),
        volumeVal: $('volumeVal'),
        quadPlayBtn: $('quadPlayBtn'),
    };

    let currentJobId = null;
    let eventSource = null;
    let syncing = false;
    let jobRunning = false;

    const allVideos = () => [els.origVideo, els.origVideoB, els.aiVideo, els.aiVideoB].filter(Boolean);
    const secondaryVideos = () => [els.origVideoB, els.aiVideo, els.aiVideoB].filter(Boolean);

    function saveJob(jobId) {
        try { localStorage.setItem(JOB_KEY, JSON.stringify({ job_id: jobId, ts: Date.now() })); } catch {}
    }

    function loadJob() {
        try { return JSON.parse(localStorage.getItem(JOB_KEY) || 'null'); } catch { return null; }
    }

    function clearJob() {
        try { localStorage.removeItem(JOB_KEY); } catch {}
    }

    function saveVideo(name) {
        try { localStorage.setItem(VIDEO_KEY, name); } catch {}
    }

    async function loadVideos() {
        try {
            const response = await fetch('/api/video-test/videos');
            const data = await response.json();
            els.videoSelect.innerHTML = '';
            (data.videos || []).forEach((video) => {
                const option = document.createElement('option');
                option.value = video.name;
                option.textContent = `${video.name} (${video.source === 'default' ? '默认' : '上传'}, ${(video.size / 1048576).toFixed(1)}MB)`;
                els.videoSelect.appendChild(option);
            });
            if (!data.videos || !data.videos.length) {
                els.videoSelect.innerHTML = '<option value="">无可用视频，请上传</option>';
            }
        } catch (error) {
            els.videoSelect.innerHTML = '<option>加载失败</option>';
        }
    }

    async function loadYoloModels() {
        try {
            const response = await fetch('/api/video-test/yolo-models');
            const data = await response.json();
            els.yoloModel.innerHTML = '';
            (data.models || []).forEach((model) => {
                const option = document.createElement('option');
                option.value = model.value;
                option.textContent = model.name;
                els.yoloModel.appendChild(option);
            });
            if (data.active) {
                const match = (data.models || []).find((model) => model.value === data.active);
                if (match) els.yoloModel.value = match.value;
            }
        } catch (error) {
            els.yoloModel.innerHTML = '<option value="yolo11n.pt">yolo11n.pt</option>';
        }
    }

    function setVideoSource(video, src) {
        if (!video) return;
        video.src = src;
        video.load();
    }

    function clearAiVideos() {
        [els.aiVideo, els.aiVideoB].filter(Boolean).forEach((video) => {
            video.removeAttribute('src');
            video.load();
        });
        els.aiBadge.textContent = '待生成';
        els.aiBadge.classList.remove('ready');
    }

    function selectVideo(name) {
        if (!name) return;
        const src = `/api/video-test/video/${encodeURIComponent(name)}`;
        setVideoSource(els.origVideo, src);
        setVideoSource(els.origVideoB, src);
        els.origVideo.muted = false;
        els.origVideoB.muted = true;
        saveVideo(name);
    }

    els.videoSelect.addEventListener('change', () => {
        const option = els.videoSelect.options[els.videoSelect.selectedIndex];
        if (option && option.value) {
            selectVideo(option.value);
            clearAiVideos();
        }
    });

    els.refreshVideos.addEventListener('click', loadVideos);

    els.uploadBtn.addEventListener('click', () => els.videoFile.click());
    els.videoFile.addEventListener('change', async () => {
        const file = els.videoFile.files[0];
        if (!file) return;
        els.uploadMsg.textContent = '上传中...';
        const formData = new FormData();
        formData.append('video', file);
        try {
            const response = await fetch('/api/video-test/upload', { method: 'POST', body: formData });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || '上传失败');
            els.uploadMsg.textContent = `✓ ${data.name} 上传成功`;
            await loadVideos();
            els.videoSelect.value = data.name;
            els.videoSelect.dispatchEvent(new Event('change'));
        } catch (error) {
            els.uploadMsg.textContent = '✗ ' + error.message;
        } finally {
            els.videoFile.value = '';
        }
    });

    function updateEnginePanel() {
        const engine = document.querySelector('input[name="engine"]:checked').value;
        els.yoloPanel.style.display = engine === 'yolo' ? '' : 'none';
        els.sam3Panel.style.display = (engine === 'sam3' || engine === 'vlm') ? '' : 'none';
        els.vlmPanel.style.display = engine === 'vlm' ? '' : 'none';
        if (engine === 'vlm' && !els.vlmModel.options.length) loadVlmModels();
    }

    async function loadVlmModels() {
        try {
            const response = await fetch('/api/vlm/models');
            const data = await response.json();
            const models = data.models || [];
            els.vlmModel.innerHTML = '';
            if (!models.length) {
                els.vlmModel.innerHTML = '<option value="">-- VLM 服务未启动或无可用模型 --</option>';
                return;
            }
            models.forEach((m) => {
                const option = document.createElement('option');
                option.value = m.id;
                option.textContent = m.label;
                els.vlmModel.appendChild(option);
            });
            const available = models.find((m) => m.available);
            if (available) els.vlmModel.value = available.id;
        } catch (error) {
            els.vlmModel.innerHTML = '<option value="">-- VLM 服务未启动或无可用模型 --</option>';
        }
    }

    let depthModels = [];

    async function loadDepthModels() {
        try {
            const response = await fetch('/api/video-test/depth-models');
            const data = await response.json();
            depthModels = data.models || [];
            els.depthModelSelect.innerHTML = '';
            depthModels.forEach((m) => {
                const option = document.createElement('option');
                option.value = m.id;
                option.textContent = m.label;
                option.dataset.metric = m.metric ? '1' : '';
                els.depthModelSelect.appendChild(option);
            });
            syncMetersToggle();
        } catch (error) {
            els.depthModelSelect.innerHTML = '<option value="depth_anything_v2_small">Depth Anything v2 Small</option>';
        }
    }

    function currentDepthMetric() {
        const option = els.depthModelSelect.selectedOptions[0];
        return !!(option && option.dataset.metric);
    }

    // 相对深度模型无米可显：开关禁用（工单 06）
    function syncMetersToggle() {
        if (!els.showMetersToggle) return;
        els.showMetersToggle.disabled = !currentDepthMetric();
        if (!currentDepthMetric()) els.showMetersToggle.checked = false;
    }

    function updateDepthPanel() {
        const modeEl = document.querySelector('input[name="taskMode"]:checked');
        const isDepth = modeEl && modeEl.value === 'depth_track';
        els.depthPanel.style.display = isDepth ? '' : 'none';
    }

    document.querySelectorAll('input[name="engine"]').forEach((radio) => radio.addEventListener('change', updateEnginePanel));
    document.querySelectorAll('input[name="taskMode"]').forEach((radio) => radio.addEventListener('change', updateDepthPanel));
    els.depthModelSelect?.addEventListener('change', syncMetersToggle);
    els.confRange?.addEventListener('input', () => { els.confVal.textContent = els.confRange.value; });
    els.volumeRange?.addEventListener('input', updateVolume);
    els.quadPlayBtn?.addEventListener('click', toggleQuadPlayback);
    els.startBtn?.addEventListener('click', () => {
        if (jobRunning) stopInference();
        else startInference();
    });

    function setRunButton(running, disabled = false) {
        jobRunning = running;
        if (!els.startBtn) return;
        els.startBtn.disabled = disabled;
        els.startBtn.classList.toggle('btn-success', !running);
        els.startBtn.classList.toggle('btn-danger', running);
        els.startBtn.innerHTML = running
            ? '<i class="fas fa-stop"></i> 停止推理'
            : '<i class="fas fa-play"></i> 开始全帧推理';
    }

    function updateVolume() {
        if (!els.volumeRange || !els.origVideo) return;
        const volume = Number(els.volumeRange.value || 0);
        els.origVideo.volume = volume;
        if (els.volumeVal) els.volumeVal.textContent = `${Math.round(volume * 100)}%`;
    }

    function startInference() {
        const engine = document.querySelector('input[name="engine"]:checked').value;
        const modeEl = document.querySelector('input[name="taskMode"]:checked');
        const mode = modeEl ? modeEl.value : 'detect';
        const videoName = els.videoSelect.value;
        if (!videoName) { alert('请先选择视频'); return; }

        const body = { video_name: videoName, engine, mode, confidence: parseFloat(els.confRange.value) };
        if (engine === 'yolo') {
            body.model = els.yoloModel.value || 'yolo11n.pt';
        } else if (engine === 'vlm') {
            body.vlm_model = els.vlmModel.value || undefined;
            body.classes = els.sam3Classes.value;
            if (!body.classes.trim()) { alert('请填写目标类别'); return; }
        } else if (engine === 'sam3') {
            body.classes = els.sam3Classes.value;
        }
        if (mode === 'depth_track' && els.depthModelSelect?.value) {
            body.depth_model = els.depthModelSelect.value;
            body.show_meters = els.showMetersToggle ? !!els.showMetersToggle.checked : true;
        }

        setRunButton(false, true);
        els.statusArea.style.display = '';
        els.statusArea.classList.remove('error', 'done');
        setProgress(0, '提交任务...');
        els.aiBadge.textContent = '推理中';
        els.aiBadge.classList.remove('ready');
        clearAiVideos();

        fetch('/api/video-test/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        }).then((response) => response.json()).then((data) => {
            if (data.error) throw new Error(data.error);
            currentJobId = data.job_id;
            saveJob(data.job_id);
            setRunButton(true);
            openStream(data.job_id);
        }).catch((error) => {
            setProgress(0, '✗ ' + error.message, true);
            setRunButton(false);
            els.aiBadge.textContent = '失败';
        });
    }

    function stopInference() {
        if (!currentJobId) {
            setRunButton(false);
            return;
        }
        setRunButton(true, true);
        setProgress(0, '正在停止推理...');
        fetch(`/api/video-test/job/${encodeURIComponent(currentJobId)}/stop`, {
            method: 'POST',
        }).then((response) => response.json()).then((data) => {
            if (data.error) throw new Error(data.error);
            if (data.status === 'stopped') handleProgress(data);
        }).catch((error) => {
            setRunButton(true);
            setProgress(0, '停止失败: ' + error.message, true);
        });
    }

    function openStream(jobId) {
        if (eventSource) eventSource.close();
        eventSource = new EventSource(`/api/video-test/stream/${jobId}`);
        eventSource.onmessage = (event) => {
            let data;
            try { data = JSON.parse(event.data); } catch { return; }
            handleProgress(data);
        };
        eventSource.onerror = () => { if (currentJobId) checkJobOnce(currentJobId); };
    }

    function handleProgress(data) {
        if (data.progress !== undefined) setProgress(data.progress, data.message || '', data.status === 'failed');
        if (data.status === 'completed') {
            closeStream();
            els.statusArea.classList.add('done');
            setProgress(100, data.message || '完成');
            setRunButton(false);
            if (data.ai_video_url) {
                setAiVideoSources(data.ai_video_url);
            }
        } else if (data.status === 'stopping') {
            setRunButton(true, true);
        } else if (data.status === 'stopped') {
            closeStream();
            clearJob();
            currentJobId = null;
            setRunButton(false);
            els.aiBadge.textContent = '已停止';
            setProgress(data.progress || 0, data.message || '已停止推理');
        } else if (data.status === 'failed') {
            closeStream();
            els.statusArea.classList.add('error');
            setRunButton(false);
            els.aiBadge.textContent = '失败';
            setProgress(data.progress || 0, '✗ ' + (data.error || data.message || '失败'), true);
        }
    }

    function setAiVideoSources(src) {
        setVideoSource(els.aiVideo, src);
        setVideoSource(els.aiVideoB, src);
        if (els.aiVideo) els.aiVideo.muted = true;
        if (els.aiVideoB) els.aiVideoB.muted = true;
        els.aiBadge.textContent = '已生成';
        els.aiBadge.classList.add('ready');
        syncAllToPrimary();
    }

    async function checkJobOnce(jobId) {
        try {
            const response = await fetch(`/api/video-test/job/${jobId}`);
            if (!response.ok) {
                if (currentJobId === jobId) {
                    closeStream();
                    clearJob();
                    currentJobId = null;
                    setRunButton(false);
                }
                return;
            }
            handleProgress(await response.json());
        } catch {}
    }

    function closeStream() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
    }

    function setProgress(percent, message, isError) {
        els.progressBar.style.width = Math.max(0, Math.min(100, percent)) + '%';
        if (message) els.statusText.textContent = message;
        if (isError) els.statusArea.classList.add('error');
    }

    async function resumeJob() {
        const saved = loadJob();
        if (!saved || !saved.job_id) return;
        let data;
        try {
            const response = await fetch(`/api/video-test/job/${saved.job_id}`);
            if (!response.ok) {
                clearJob();
                currentJobId = null;
                setRunButton(false);
                return;
            }
            data = await response.json();
        } catch { clearJob(); return; }
        if (!data || data.error || !data.status) { clearJob(); return; }

        currentJobId = saved.job_id;
        els.statusArea.style.display = '';
        if (data.status === 'running' || data.status === 'queued' || data.status === 'stopping') {
            setRunButton(true, data.status === 'stopping');
            els.aiBadge.textContent = '推理中';
            setProgress(data.progress || 0, data.message || '恢复进度...');
            openStream(saved.job_id);
        } else if (data.status === 'completed') {
            els.statusArea.classList.add('done');
            setProgress(100, data.message || '已完成');
            if (data.ai_video_url) setAiVideoSources(data.ai_video_url);
        } else if (data.status === 'failed') {
            els.statusArea.classList.add('error');
            els.aiBadge.textContent = '失败';
            setProgress(data.progress || 0, '✗ ' + (data.error || '失败'), true);
        } else if (data.status === 'stopped') {
            clearJob();
            currentJobId = null;
            setRunButton(false);
            els.aiBadge.textContent = '已停止';
            setProgress(data.progress || 0, data.message || '已停止推理');
        }
    }

    function syncAllToPrimary() {
        if (!els.syncToggle.checked || syncing) return;
        syncing = true;
        const primary = els.origVideo;
        secondaryVideos().forEach((video) => {
            if (!video.src) return;
            video.playbackRate = primary.playbackRate;
            if (Math.abs(video.currentTime - primary.currentTime) > 0.2) {
                video.currentTime = primary.currentTime;
            }
        });
        syncing = false;
    }

    function syncPlayState(playing) {
        if (!els.syncToggle.checked || syncing) return;
        syncing = true;
        const videos = allVideos().filter((video) => video.src);
        const actions = videos.map((video) => playing ? video.play().catch(() => {}) : (video.pause(), Promise.resolve()));
        Promise.allSettled(actions).finally(() => { syncing = false; });
    }

    function toggleQuadPlayback() {
        if (els.origVideo.paused) {
            syncAllToPrimary();
            syncPlayState(true);
        } else {
            syncPlayState(false);
        }
    }

    els.origVideo.addEventListener('play', () => syncPlayState(true));
    els.origVideo.addEventListener('pause', () => syncPlayState(false));
    els.origVideo.addEventListener('seeked', syncAllToPrimary);
    els.origVideo.addEventListener('ratechange', syncAllToPrimary);
    els.origVideo.addEventListener('timeupdate', () => {
        if (!els.syncToggle.checked || syncing) return;
        secondaryVideos().forEach((video) => {
            if (video.src && Math.abs(video.currentTime - els.origVideo.currentTime) > 0.35) {
                video.currentTime = els.origVideo.currentTime;
            }
        });
    });
    els.syncToggle.addEventListener('change', () => {
        if (els.syncInfo) els.syncInfo.textContent = els.syncToggle.checked ? '四屏同步已开启' : '四屏同步已关闭';
        if (els.syncToggle.checked) syncAllToPrimary();
    });

    (async function init() {
        updateEnginePanel();
        updateVolume();
        updateDepthPanel();
        await loadVideos();
        await loadYoloModels();
        await loadDepthModels();
        loadVlmModels();
        const savedVideo = localStorage.getItem(VIDEO_KEY);
        const options = [...els.videoSelect.options].map((option) => option.value).filter(Boolean);
        const target = (savedVideo && options.includes(savedVideo)) ? savedVideo : options[0];
        if (target) {
            els.videoSelect.value = target;
            selectVideo(target);
        }
        await resumeJob();
    })();
})();
