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
