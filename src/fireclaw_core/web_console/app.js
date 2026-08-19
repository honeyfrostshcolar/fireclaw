/**
 * FireClaw Web Console - ES Module Application
 *
 * Features:
 * - State Management (activeTab, robot, taskDraft, activeExecution, recovery)
 * - Page 1: Overview & Readiness Polling
 * - Page 2: Dispatch & Natural Language Intent Understanding & Preview Confirmation
 * - Page 3: Execution Real-time Monitoring & Strict 3-State Cancel Machine (cancel_requested -> stopping -> stopped_confirmed)
 * - Page 4: Operator-confirmed admission recovery request (prototype)
 * - Page 5: Experimental Settings & Diagnostics
 * - 4-Part Structured Error Modal & Global Toast Notifications
 * - SSE EventStream Subscription with Cursor Tracking & Auto-reconnect
 */

export class WebConsoleApp {
  constructor() {
    // 1. UI State Model (physical authority remains with robot runtime evidence)
    this.state = {
      activeTab: 'overview',
      robot: {
        readiness: null,
        mode: 'unknown',
        status: 'unknown',
        activeRobotId: 'UNKNOWN',
        phase: 'UNKNOWN',
        safeState: 'unknown',
        summary: '等待 Gateway readiness 证据',
      },
      taskDraft: {
        rawInput: '',
        isParsing: false,
        parsedIntent: null, // { intent, targetRobot, estimatedSteps, riskLevel, rawPlan }
        isConfirmed: false,
      },
      activeExecution: {
        taskId: '',
        status: 'idle', // 'idle' | 'running' | 'paused' | 'cancelling' | 'cancelled' | 'completed' | 'failed'
        cancelState: 'none', // 'none' | 'cancel_requested' | 'stopping' | 'stopped_confirmed'
        timeline: [],
        currentStep: null,
        elapsedSeconds: 0,
      },
      recovery: {
        isFrozen: false,
        freezeReason: '',
        evidence: null,
        operatorSafeAcknowledged: false,
      },
      profilePath: null,
      gatewayUrl: window.location.origin || 'http://127.0.0.1:8765',
      config: {
        templates: [],
        schemas: {},
        snapshots: [],
        currentProfileToml: '',
        pendingToml: '',
      },
    };

    // Keep compatibility aliases
    this.currentTab = 'overview';
    this.readiness = null;

    // Stream & Timer handles
    this.eventSource = null;
    this.lastEventId = 0;
    this.elapsedTimer = null;
    this.pollInterval = null;

    this.init();
  }

  // -------------------------------------------------------------------------
  // Initialization & Event Binding
  // -------------------------------------------------------------------------

  init() {
    this.bindNavigation();
    this.bindOverviewEvents();
    this.bindDispatchEvents();
    this.bindExecutionEvents();
    this.bindRecoveryEvents();
    this.bindSettingsEvents();
    this.bindModalEvents();

    // Initial data fetch & SSE stream
    this.fetchReadiness();
    this.subscribeEvents();

    // Initial data fetch for the experimental configuration assistant
    this.loadTemplates();
    this.renderDynamicSchemaForm();
    this.loadSnapshots();

    // Setup periodic readiness poll (every 5 seconds)
    this.pollInterval = setInterval(() => {
      if (this.state.activeTab === 'overview' || this.state.activeTab === 'recovery') {
        this.fetchReadiness(true);
      }
    }, 5000);
  }

  // -------------------------------------------------------------------------
  // Tab Navigation
  // -------------------------------------------------------------------------

  bindNavigation() {
    const tabs = document.querySelectorAll('.nav-tab');
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        const targetTab = tab.dataset.tab;
        if (targetTab) {
          this.switchTab(targetTab);
        }
      });
    });
  }

  switchTab(tabName) {
    this.state.activeTab = tabName;
    this.currentTab = tabName;

    // Update active nav buttons
    document.querySelectorAll('.nav-tab').forEach((tab) => {
      tab.classList.toggle('active', tab.dataset.tab === tabName);
    });

    // Update active tab pane
    document.querySelectorAll('.tab-pane').forEach((pane) => {
      pane.classList.toggle('active', pane.id === `tab-${tabName}`);
    });

    // Tab-specific triggers
    if (tabName === 'overview' || tabName === 'recovery') {
      this.fetchReadiness();
    }
  }

  // -------------------------------------------------------------------------
  // Page 1: Overview & Readiness
  // -------------------------------------------------------------------------

  bindOverviewEvents() {
    const primaryActionBtn = document.getElementById('btn-primary-action');
    if (primaryActionBtn) {
      primaryActionBtn.addEventListener('click', () => {
        if (this.state.recovery.isFrozen) {
          this.switchTab('recovery');
        } else if (this.state.activeExecution.status === 'running') {
          this.switchTab('execution');
        } else {
          this.switchTab('dispatch');
        }
      });
    }
  }

  async fetchReadiness(silent = false) {
    try {
      const response = await fetch('/readiness');
      if (!response.ok) {
        throw new Error(`Readiness check failed with status: ${response.status}`);
      }
      const data = await response.json();
      this.readiness = data;
      this.state.robot.readiness = data;

      // Extract details
      const activeRobot = data.active_robot_id || 'UNKNOWN';
      const runtimeMode = data.runtime_mode || data.deployment_mode || 'unknown';
      const phase = (data.phase || 'unknown').toUpperCase();
      const safeState = data.safe_state || 'unknown';
      const summary = data.summary || 'Gateway 未提供 readiness 结论';
      const normalizedSafeState = safeState.toLowerCase();
      const isBlocked =
        data.status !== 'ok' ||
        phase !== 'READY' ||
        normalizedSafeState === 'unknown' ||
        normalizedSafeState === 'motion_blocked' ||
        normalizedSafeState === 'blocked';

      this.state.robot.activeRobotId = activeRobot;
      this.state.robot.mode = String(runtimeMode).toLowerCase();
      this.state.robot.phase = phase;
      this.state.robot.safeState = safeState;
      this.state.robot.summary = summary;
      this.state.recovery.isFrozen = isBlocked;
      this.state.recovery.freezeReason = isBlocked ? summary : '';

      this.renderOverview(data);
      this.renderRecovery(data);
    } catch (err) {
      if (!silent) {
        console.error('Failed to fetch readiness:', err);
      }
      const summaryEl = document.getElementById('overview-summary');
      if (summaryEl) {
        summaryEl.textContent = '无法连接 Gateway 就绪检测服务。';
      }
      const badgeEl = document.getElementById('overview-readiness-badge');
      if (badgeEl) {
        badgeEl.textContent = '网关离线';
        badgeEl.className = 'badge badge-danger';
      }
    }
  }

  renderOverview(data) {
    const phase = (data.phase || 'UNKNOWN').toUpperCase();
    const safeState = data.safe_state || 'unknown';
    const activeRobot = data.active_robot_id || 'UNKNOWN';
    const summary = data.summary || 'Gateway 未提供 readiness 结论；不要推断机器人物理状态。';
    const normalizedSafeState = safeState.toLowerCase();
    const isOk =
      data.status === 'ok' &&
      phase === 'READY' &&
      normalizedSafeState !== 'unknown' &&
      normalizedSafeState !== 'motion_blocked' &&
      normalizedSafeState !== 'blocked';

    // Header elements
    const robotIdDisplay = document.getElementById('robot-id-display');
    const robotLabelEl = document.getElementById('active-robot-label');
    if (robotIdDisplay) robotIdDisplay.textContent = activeRobot;
    if (robotLabelEl) robotLabelEl.textContent = activeRobot;

    const modeBadge = document.getElementById('mode-badge');
    const modeText = document.getElementById('system-mode-text');
    const overviewMode = document.getElementById('overview-mode');
    const runtimeMode = String(
      data.runtime_mode || data.deployment_mode || this.state.robot.mode || 'unknown',
    ).toLowerCase();
    if (modeBadge) modeBadge.dataset.mode = runtimeMode;
    if (modeText) modeText.textContent = runtimeMode.toUpperCase();
    if (overviewMode) overviewMode.textContent = runtimeMode.toUpperCase();

    const globalSafetyPill = document.getElementById('global-safety-pill');
    const globalSafetyText = document.getElementById('global-safety-text');
    if (globalSafetyPill && globalSafetyText) {
      if (isOk) {
        globalSafetyPill.className = 'safety-pill ready';
        globalSafetyText.textContent = 'READY';
      } else {
        globalSafetyPill.className = 'safety-pill blocked';
        globalSafetyText.textContent = 'BLOCKED';
      }
    }

    // Overview card elements
    const activeRobotEl = document.getElementById('overview-active-robot');
    const phaseEl = document.getElementById('overview-phase');
    const safeStateEl = document.getElementById('overview-safe-state');
    const summaryEl = document.getElementById('overview-summary');
    const badgeEl = document.getElementById('overview-readiness-badge');

    if (activeRobotEl) activeRobotEl.textContent = activeRobot;
    if (phaseEl) phaseEl.textContent = phase;
    if (safeStateEl) safeStateEl.textContent = safeState.toUpperCase();
    if (summaryEl) summaryEl.textContent = summary;
    if (badgeEl) {
      badgeEl.textContent = isOk ? '就绪正常' : '准入阻断';
      badgeEl.className = isOk ? 'badge badge-success' : 'badge badge-danger';
    }

    // Safety card
    const estopBadge = document.getElementById('estop-indicator-badge');
    if (estopBadge) {
      const physicalSafety = data.physical_safety || {};
      if (physicalSafety.e_stop_engaged === true) {
        estopBadge.textContent = 'Gateway 报告 E-STOP 已触发';
        estopBadge.className = 'badge badge-danger';
      } else if (physicalSafety.e_stop_released === true) {
        estopBadge.textContent = 'Gateway 报告 E-STOP 已释放（时效未验证）';
        estopBadge.className = 'badge badge-warning';
      } else {
        estopBadge.textContent = 'E-STOP 未知';
        estopBadge.className = 'badge badge-warning';
      }
    }

    // Recommended Action Card
    const recLevel = document.getElementById('recommendation-level');
    const recDesc = document.getElementById('recommendation-desc');
    const primaryBtn = document.getElementById('btn-primary-action');

    if (recLevel && recDesc && primaryBtn) {
      primaryBtn.disabled = false;
      if (!isOk) {
        recLevel.textContent = '安全阻断';
        recLevel.className = 'badge badge-danger';
        recDesc.textContent = 'Gateway 准入投影为阻断；请人工核实。恢复请求不能证明机器人物理安全。';
        primaryBtn.textContent = '前往恢复中心';
        primaryBtn.className = 'btn btn-warning';
      } else if (this.state.activeExecution.status === 'running') {
        recLevel.textContent = '监控中';
        recLevel.className = 'badge badge-primary';
        recDesc.textContent = `任务正在执行中 (ID: ${this.state.activeExecution.taskId})，点击查看实时进度与状态。`;
        primaryBtn.textContent = '查看执行进度';
        primaryBtn.className = 'btn btn-primary';
      } else {
        recLevel.textContent = '建议下发';
        recLevel.className = 'badge badge-info';
        recDesc.textContent = 'Gateway 当前准入投影为 READY；这不构成物理安全证明。';
        primaryBtn.textContent = '下发新任务';
        primaryBtn.className = 'btn btn-primary';
      }
    }

    // Fleet List
    const fleetListEl = document.getElementById('overview-fleet-list');
    const fleetCountBadge = document.getElementById('fleet-count-badge');
    if (data.fleet_state && data.fleet_state.entries && fleetListEl) {
      const entries = data.fleet_state.entries;
      if (fleetCountBadge) fleetCountBadge.textContent = `${entries.length} 条 Gateway 记录`;
      fleetListEl.innerHTML = entries
        .map(
          (entry) => `
        <li class="fleet-item">
          <div class="fleet-item-info">
            <span class="fleet-item-id">${this.escapeHtml(entry.robot_id)}</span>
            <span class="fleet-item-caps">${this.escapeHtml(entry.capabilities ? entry.capabilities.join(', ') : '能力未报告')}</span>
          </div>
          <span class="badge ${entry.is_online ? 'badge-success' : 'badge-danger'}">
            ${entry.is_online ? 'ONLINE' : 'OFFLINE'}
          </span>
        </li>
      `
        )
        .join('');
    }
  }

  // -------------------------------------------------------------------------
  // Page 2: Dispatch & Intent Understanding
  // -------------------------------------------------------------------------

  bindDispatchEvents() {
    // Template chips click
    const chips = document.querySelectorAll('.template-chip');
    chips.forEach((chip) => {
      chip.addEventListener('click', () => {
        const text = chip.dataset.template || chip.textContent.trim();
        const input = document.getElementById('task-input-text');
        const cmdInput = document.getElementById('command-input');
        if (input) input.value = text;
        if (cmdInput) cmdInput.value = text;
      });
    });

    // Parse Task button
    const parseBtn = document.getElementById('btn-parse-task');
    const parseIntentBtn = document.getElementById('btn-parse-intent');
    if (parseBtn) parseBtn.addEventListener('click', () => this.parseTaskIntent());
    if (parseIntentBtn) parseIntentBtn.addEventListener('click', () => this.parseTaskIntent());

    // Confirm Start button
    const confirmBtn = document.getElementById('btn-confirm-start');
    if (confirmBtn) confirmBtn.addEventListener('click', () => this.confirmAndStartTask());

    // Cancel Preview button
    const cancelPreviewBtn = document.getElementById('btn-cancel-preview');
    if (cancelPreviewBtn) cancelPreviewBtn.addEventListener('click', () => this.cancelTaskPreview());

    // Form submit may create a preview, but never auto-confirms it.
    const dispatchForm = document.getElementById('dispatch-form');
    if (dispatchForm) {
      dispatchForm.addEventListener('submit', (e) => {
        e.preventDefault();
        this.parseTaskIntent();
      });
    }
  }

  async parseTaskIntent() {
    const inputEl = document.getElementById('task-input-text');
    const rawText = (inputEl ? inputEl.value : '').trim();

    if (!rawText) {
      this.showToast('请输入搜救任务自然语言指令', 'warning');
      return;
    }

    this.state.taskDraft.rawInput = rawText;
    this.state.taskDraft.isParsing = true;

    const parseBtn = document.getElementById('btn-parse-task');
    const originalText = parseBtn ? parseBtn.textContent : '解析任务';
    if (parseBtn) {
      parseBtn.disabled = true;
      parseBtn.textContent = '正在解析意图...';
    }

    try {
      const response = await fetch('/plan-mission', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify({
          task_description: rawText,
          profile: this.state.profilePath,
        }),
      });

      if (!response.ok) {
        await this.handleApiError(response, 'planner_failed');
        return;
      }

      const data = await response.json();
      this.state.taskDraft.parsedIntent = {
        intent: data.intent || rawText,
        targetRobot: data.target_robot || this.state.robot.activeRobotId || 'UNKNOWN',
        estimatedSteps: data.steps || [],
        riskLevel: data.risk_level || 'medium',
        rawPlan: data.raw_plan || data,
      };

      this.renderIntentPreview();

      this.showToast('意图解析完成，请核对规划步骤后显式确认', 'info');
    } catch (err) {
      console.error('Failed to parse task intent:', err);
      await this.handleApiError(err, 'planner_failed');
    } finally {
      this.state.taskDraft.isParsing = false;
      if (parseBtn) {
        parseBtn.disabled = false;
        parseBtn.textContent = originalText;
      }
    }
  }

  renderIntentPreview() {
    const card = document.getElementById('intent-preview-card');
    const parsedIntent = this.state.taskDraft.parsedIntent;
    if (!card || !parsedIntent) return;

    // Populating parsed intent elements
    const taskEl = document.getElementById('preview-parsed-task');
    const goalTextEl = document.getElementById('intent-goal-text');
    const robotEl = document.getElementById('preview-target-robot');
    const stepsEl = document.getElementById('preview-estimated-steps');
    const subtasksListEl = document.getElementById('intent-subtasks-list');
    const riskEl = document.getElementById('preview-risk-level');
    const riskLevelEl = document.getElementById('intent-risk-level');

    if (taskEl) taskEl.textContent = parsedIntent.intent;
    if (goalTextEl) goalTextEl.textContent = parsedIntent.intent;
    if (robotEl) robotEl.textContent = parsedIntent.targetRobot;

    const formattedSteps = Array.isArray(parsedIntent.estimatedSteps)
      ? parsedIntent.estimatedSteps.join(' → ')
      : parsedIntent.estimatedSteps;
    if (stepsEl) stepsEl.textContent = formattedSteps;

    if (subtasksListEl && Array.isArray(parsedIntent.estimatedSteps)) {
      subtasksListEl.innerHTML = parsedIntent.estimatedSteps
        .map((step) => `<li>${this.escapeHtml(step)}</li>`)
        .join('');
    }

    const riskText = parsedIntent.riskLevel === 'high' ? '高风险' : parsedIntent.riskLevel === 'medium' ? '中等风险' : '低风险';
    if (riskEl) {
      riskEl.textContent = `${riskText}（涉及未知区域探测）`;
      riskEl.className = `intent-val risk-${parsedIntent.riskLevel}`;
    }
    if (riskLevelEl) riskLevelEl.textContent = riskText;

    card.style.display = 'block';
  }

  cancelTaskPreview() {
    const card = document.getElementById('intent-preview-card');
    if (card) card.style.display = 'none';
    this.state.taskDraft.parsedIntent = null;
    const inputEl = document.getElementById('task-input-text');
    if (inputEl) {
      inputEl.disabled = false;
      inputEl.focus();
    }
  }

  async confirmAndStartTask() {
    const draft = this.state.taskDraft;
    if (!draft.parsedIntent || draft.isConfirmed) {
      this.showToast('必须先生成计划预览，并由操作员执行一次显式确认', 'warning');
      return;
    }
    draft.isConfirmed = true;
    const intent = draft.parsedIntent ? draft.parsedIntent.intent : draft.rawInput;
    const robotId = draft.parsedIntent ? draft.parsedIntent.targetRobot : this.state.robot.activeRobotId;

    const confirmBtn = document.getElementById('btn-confirm-start');
    if (confirmBtn) confirmBtn.disabled = true;

    try {
      const response = await fetch('/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify({
          command: draft.rawInput || intent,
          task: draft.rawInput || intent,
          goal: intent,
          target_robot: robotId,
          plan: draft.parsedIntent ? draft.parsedIntent.rawPlan : null,
        }),
      });

      if (!response.ok) {
        await this.handleApiError(response, 'action_failed');
        return;
      }

      const data = await response.json();
      const taskId = data.task_id || data.mission_id || 'UNKNOWN';

      // Update active execution state
      this.state.activeExecution.taskId = taskId;
      this.state.activeExecution.status = 'accepted';
      this.state.activeExecution.cancelState = 'none';
      this.state.activeExecution.timeline = [];
      this.state.activeExecution.elapsedSeconds = 0;

      // Update UI in Execution Tab
      const missionIdEl = document.getElementById('exec-mission-id');
      const assignedRobotEl = document.getElementById('exec-assigned-robot');
      const statusBadge = document.getElementById('execution-status-badge');
      const execStatusEl = document.getElementById('exec-status');

      if (missionIdEl) missionIdEl.textContent = taskId;
      if (assignedRobotEl) assignedRobotEl.textContent = robotId;
      if (statusBadge) {
        statusBadge.textContent = 'ACCEPTED';
        statusBadge.className = 'badge badge-info';
      }
      if (execStatusEl) execStatusEl.textContent = 'ACCEPTED';

      // Reset Stepper
      this.resetCancelStepperUI();

      // Record only the local submission acknowledgement. Execution state must
      // come from Gateway/Robot runtime events.
      this.addTimelineEvent({
        id: `evt-${Date.now()}`,
        time: new Date().toLocaleTimeString(),
        type: 'ui.submission.accepted',
        title: 'Gateway 已接受任务请求',
        detail: `指令: "${intent}" | 请求机器人: ${robotId} | 等待权威执行事件`,
      });

      // Do not invent planner/runtime progress while waiting for evidence.
      this.updateCurrentStepUI({
        name: '等待 Gateway / Robot runtime 执行事件',
        percent: 0,
        feedback: '请求已接受；尚未收到机器人开始运动或 Tool 执行证据。',
        tool: 'UNKNOWN',
      });

      // Clear draft preview
      this.cancelTaskPreview();

      // Switch to execution tab
      this.switchTab('execution');
      this.showToast('Gateway 已接受任务请求；尚未确认机器人开始执行', 'info');
    } catch (err) {
      console.error('Failed to submit task:', err);
      await this.handleApiError(err, 'action_failed');
    } finally {
      draft.isConfirmed = false;
      if (confirmBtn) confirmBtn.disabled = false;
    }
  }

  // -------------------------------------------------------------------------
  // Page 3: Execution & 3-State Cancel Machine
  // -------------------------------------------------------------------------

  bindExecutionEvents() {
    const cancelBtn = document.getElementById('btn-cancel-task');
    const cancelMissionBtn = document.getElementById('btn-cancel-mission');
    if (cancelBtn) cancelBtn.addEventListener('click', () => this.requestTaskCancellation());
    if (cancelMissionBtn) cancelMissionBtn.addEventListener('click', () => this.requestTaskCancellation());

    const pauseBtn = document.getElementById('btn-pause-task');
    if (pauseBtn) {
      pauseBtn.addEventListener('click', () => this.togglePauseTask());
    }

    const resumeBtn = document.getElementById('btn-resume-task');
    if (resumeBtn) {
      resumeBtn.addEventListener('click', () => this.togglePauseTask());
    }
  }

  startElapsedTimer() {
    if (this.elapsedTimer) clearInterval(this.elapsedTimer);
    this.state.activeExecution.elapsedSeconds = 0;
    const timeEl = document.getElementById('exec-elapsed-time');

    this.elapsedTimer = setInterval(() => {
      this.state.activeExecution.elapsedSeconds += 1;
      const s = this.state.activeExecution.elapsedSeconds;
      const mm = String(Math.floor(s / 60)).padStart(2, '0');
      const ss = String(s % 60).padStart(2, '0');
      if (timeEl) timeEl.textContent = `00:${mm}:${ss}`;
    }, 1000);
  }

  stopElapsedTimer() {
    if (this.elapsedTimer) {
      clearInterval(this.elapsedTimer);
      this.elapsedTimer = null;
    }
  }

  /**
   * Strict 3-State Cancel Machine:
   * State 1: cancel_requested (Triggered on cancel button click -> POST /tasks/<id>/cancel)
   * State 2: stopping         (Triggered only by authoritative robot.stopping feedback)
   * State 3: stopped_confirmed (Requires an authoritative stopped event with physical stop evidence)
   */
  async requestTaskCancellation() {
    const taskId = this.state.activeExecution.taskId;
    if (!taskId) {
      this.showToast('当前没有正在执行的任务', 'warning');
      return;
    }

    // Step 1: cancel_requested
    this.updateCancelState('cancel_requested');

    try {
      const response = await fetch(`/tasks/${encodeURIComponent(taskId)}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify({
          operator_confirmed: true,
          reason: '指挥员手动取消',
        }),
      });

      if (!response.ok) {
        await this.handleApiError(response, 'task_cancelled');
        this.updateCancelState('none');
        return;
      }

      this.addTimelineEvent({
        id: `cancel-${Date.now()}`,
        time: new Date().toLocaleTimeString(),
        type: 'task.cancel_requested',
        title: '取消请求已送达网关',
        detail: '任务层已接受取消请求；尚未收到机器人正在制动或已经停止的物理证据。',
      });

      // Strict physical safety: Do not fake stopped_confirmed via timer.
      // If no stop confirmation is received within 15s, alert the operator.
      setTimeout(() => {
        if (
          this.state.activeExecution.cancelState === 'cancel_requested' ||
          this.state.activeExecution.cancelState === 'stopping'
        ) {
          this.showToast('尚未收到物理停止确认，请密切关注底盘状态，必要时可拍急停', 'warning');
        }
      }, 15000);
    } catch (err) {
      console.error('Cancellation request failed:', err);
      await this.handleApiError(err, 'task_cancelled');
      // Rollback cancel state so operator can retry
      this.updateCancelState('none');
    }
  }

  updateCancelState(newState) {
    this.state.activeExecution.cancelState = newState;

    const step1 = document.getElementById('cancel-step-1');
    const step2 = document.getElementById('cancel-step-2');
    const step3 = document.getElementById('cancel-step-3');
    const cancelText = document.getElementById('cancel-status-text');

    if (newState === 'cancel_requested') {
      if (step1) {
        step1.className = 'cancel-step-item active current';
      }
      if (step2) step2.className = 'cancel-step-item';
      if (step3) step3.className = 'cancel-step-item';
      if (cancelText) cancelText.textContent = '取消请求已发送，等待底盘响应...';

      const cancelBtn = document.getElementById('btn-cancel-task');
      if (cancelBtn) cancelBtn.disabled = true;

      this.addTimelineEvent({
        id: `evt-${Date.now()}`,
        time: new Date().toLocaleTimeString(),
        type: 'cancel_requested',
        title: '取消三态 #1: cancel_requested',
        detail: '取消请求已发出；此状态不代表制动已经开始，也不代表机器人已经停止。',
      });
    } else if (newState === 'stopping') {
      if (step1) step1.className = 'cancel-step-item completed';
      if (step2) step2.className = 'cancel-step-item active current';
      if (step3) step3.className = 'cancel-step-item';
      if (cancelText) cancelText.textContent = '机器人正在减速制动中...';

      this.addTimelineEvent({
        id: `evt-${Date.now()}`,
        time: new Date().toLocaleTimeString(),
        type: 'stopping',
        title: '取消三态 #2: stopping',
        detail: '已收到机器人运行时的制动中反馈；继续等待带停止证据的最终确认。',
      });
    } else if (newState === 'stopped_confirmed') {
      if (step1) step1.className = 'cancel-step-item completed';
      if (step2) step2.className = 'cancel-step-item completed';
      if (step3) step3.className = 'cancel-step-item completed active';
      if (cancelText) cancelText.textContent = '机器人已确认物理停止';

      this.state.activeExecution.status = 'cancelled';
      this.stopElapsedTimer();

      const statusBadge = document.getElementById('execution-status-badge');
      if (statusBadge) {
        statusBadge.textContent = 'CANCELLED';
        statusBadge.className = 'badge badge-warning';
      }

      this.addTimelineEvent({
        id: `evt-${Date.now()}`,
        time: new Date().toLocaleTimeString(),
        type: 'stopped_confirmed',
        title: '取消三态 #3: stopped_confirmed',
        detail: '已收到运行时明确标记的物理停止确认和停止证据。',
      });

      this.showToast('任务已取消，并收到物理停止证据', 'warning');
    }
  }

  resetCancelStepperUI() {
    const step1 = document.getElementById('cancel-step-1');
    const step2 = document.getElementById('cancel-step-2');
    const step3 = document.getElementById('cancel-step-3');
    const cancelText = document.getElementById('cancel-status-text');

    if (step1) step1.className = 'cancel-step-item';
    if (step2) step2.className = 'cancel-step-item';
    if (step3) step3.className = 'cancel-step-item';
    if (cancelText) cancelText.textContent = '运行中';

    const cancelBtn = document.getElementById('btn-cancel-task');
    if (cancelBtn) cancelBtn.disabled = false;
  }

  async togglePauseTask() {
    const taskId = this.state.activeExecution.taskId;
    if (!taskId) return;
    const isPaused = this.state.activeExecution.status === 'paused';
    const endpoint = isPaused ? `/tasks/${encodeURIComponent(taskId)}/resume` : `/tasks/${encodeURIComponent(taskId)}/pause`;

    try {
      const resp = await fetch(endpoint, { method: 'POST', body: '{}' });
      if (resp.ok) {
        this.state.activeExecution.status = isPaused ? 'running' : 'paused';
        const pauseBtn = document.getElementById('btn-pause-task');
        if (pauseBtn) pauseBtn.textContent = isPaused ? '暂停任务' : '继续任务';
        const statusBadge = document.getElementById('execution-status-badge');
        if (statusBadge) {
          statusBadge.textContent = isPaused ? 'RUNNING' : 'PAUSED';
          statusBadge.className = isPaused ? 'badge badge-primary' : 'badge badge-warning';
        }
        this.showToast(isPaused ? '任务已恢复执行' : '任务已暂停', 'info');
      }
    } catch (err) {
      console.error('Failed to toggle pause:', err);
    }
  }

  updateCurrentStepUI(step) {
    this.state.activeExecution.currentStep = step;
    const toolEl = document.getElementById('current-action-tool');
    const nameEl = document.getElementById('current-action-name');
    const percentEl = document.getElementById('current-action-percent');
    const progressEl = document.getElementById('current-action-progress');
    const feedbackEl = document.getElementById('current-action-feedback');

    if (toolEl && step.tool) toolEl.textContent = step.tool;
    if (nameEl && step.name) nameEl.textContent = step.name;
    if (percentEl && step.percent !== undefined) percentEl.textContent = `${step.percent}%`;
    if (progressEl && step.percent !== undefined) progressEl.style.width = `${step.percent}%`;
    if (feedbackEl && step.feedback) feedbackEl.textContent = step.feedback;
  }

  addTimelineEvent(event) {
    this.state.activeExecution.timeline.push(event);
    const container = document.getElementById('execution-timeline');
    if (!container) return;

    const empty = container.querySelector('.timeline-empty');
    if (empty) empty.remove();

    const item = document.createElement('div');
    item.className = 'timeline-item';
    item.innerHTML = `
      <div class="timeline-time">${this.escapeHtml(event.time || new Date().toLocaleTimeString())}</div>
      <div class="timeline-dot"></div>
      <div class="timeline-content">
        <div class="timeline-title">${this.escapeHtml(event.title || event.type)}</div>
        <div class="timeline-detail">${this.escapeHtml(event.detail || '')}</div>
      </div>
    `;

    container.appendChild(item);
    container.scrollTop = container.scrollHeight;
  }

  // -------------------------------------------------------------------------
  // SSE EventStream Subscription
  // -------------------------------------------------------------------------

  subscribeEvents() {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }

    const streamStatus = document.getElementById('sse-stream-status');
    const url = `/events?cursor=${this.lastEventId}`;

    try {
      this.eventSource = new EventSource(url);

      this.eventSource.onopen = () => {
        if (streamStatus) {
          streamStatus.textContent = 'SSE Connected';
          streamStatus.className = 'badge badge-success';
        }
      };

      this.eventSource.onerror = (err) => {
        if (streamStatus) {
          streamStatus.textContent = 'SSE Reconnecting...';
          streamStatus.className = 'badge badge-warning';
        }
      };

      const eventTypes = [
        'task.progress',
        'task.step',
        'mission.subtask_dispatched',
        'task.cancel_requested',
        'mission.cancel_requested',
        'task.cancelling',
        'robot.stopping',
        'task.cancelled',
        'task.stopped',
        'robot.stopped_confirmed',
        'mission.cancelled',
        'task.completed',
        'mission.completed',
        'mission.admission_projection_reset',
        'safety.recovered',
        'robot.frozen',
        'heartbeat',
      ];

      const onEventData = (e) => {
        try {
          if (!e.data || e.data.trim() === ': heartbeat') return;
          const data = JSON.parse(e.data);
          if (e.lastEventId) {
            this.lastEventId = parseInt(e.lastEventId, 10) || this.lastEventId;
          } else if (data.sequence) {
            this.lastEventId = data.sequence;
          }
          this.handleStreamEvent(data);
        } catch (err) {
          // Ignore JSON parse errors for heartbeats
        }
      };

      this.eventSource.onmessage = onEventData;
      eventTypes.forEach((type) => {
        this.eventSource.addEventListener(type, onEventData);
      });
    } catch (err) {
      console.error('Failed to initialize EventSource:', err);
    }
  }

  handleStreamEvent(event) {
    const type = event.event_type || event.type || '';
    const payload = event.payload || {};

    if (type === 'task.progress' || type === 'task.step' || type === 'mission.subtask_dispatched') {
      const stepName = payload.step_name || payload.command || payload.subtask_id || '未命名执行事件';
      const percent = payload.percent !== undefined ? payload.percent : 0;
      const feedback = payload.feedback || payload.status || 'Gateway 事件未提供执行反馈';
      const tool = payload.tool || payload.capability || 'UNKNOWN';

      this.updateCurrentStepUI({ name: stepName, percent, feedback, tool });
      this.addTimelineEvent({
        id: `evt-${Date.now()}`,
        time: new Date().toLocaleTimeString(),
        type,
        title: `步骤推进: ${stepName}`,
        detail: feedback,
      });
    } else if (
      type === 'task.cancel_requested' ||
      type === 'mission.cancel_requested' ||
      type === 'task.cancelling'
    ) {
      if (this.state.activeExecution.cancelState === 'none') {
        this.updateCancelState('cancel_requested');
      }
    } else if (type === 'robot.stopping') {
      if (
        this.state.activeExecution.cancelState === 'cancel_requested' ||
        this.state.activeExecution.cancelState === 'none'
      ) {
        this.updateCancelState('stopping');
      }
    } else if (type === 'task.stopped' || type === 'robot.stopped_confirmed') {
      const hasPhysicalStopEvidence =
        payload.physical_stop_confirmed === true &&
        payload.stop_evidence &&
        typeof payload.stop_evidence === 'object';
      if (hasPhysicalStopEvidence && this.state.activeExecution.cancelState !== 'stopped_confirmed') {
        this.updateCancelState('stopped_confirmed');
      } else if (!hasPhysicalStopEvidence) {
        this.addTimelineEvent({
          id: `evt-${Date.now()}`,
          time: new Date().toLocaleTimeString(),
          type: 'stop_confirmation_rejected',
          title: '停止事件缺少物理证据',
          detail: '界面未进入“已确认停止”；请检查机器人适配器或现场急停状态。',
        });
        this.showToast('收到停止事件，但缺少可验证的物理停止证据', 'warning');
      }
    } else if (type === 'task.cancelled' || type === 'mission.cancelled') {
      this.addTimelineEvent({
        id: `evt-${Date.now()}`,
        time: new Date().toLocaleTimeString(),
        type,
        title: '任务层取消已完成',
        detail: '任务状态已取消，但机器人是否已物理停止仍待运行时证据确认。',
      });
      this.showToast('任务已取消；机器人物理停止尚未确认', 'warning');
    } else if (
      type === 'mission.admission_projection_reset' ||
      type === 'safety.recovered'
    ) {
      this.fetchReadiness(true);
    }
  }

  // -------------------------------------------------------------------------
  // Page 4: Operator-confirmed admission projection reset (prototype)
  // -------------------------------------------------------------------------

  bindRecoveryEvents() {
    const confirmCheck = document.getElementById('recovery-confirm-check');
    const executeBtn = document.getElementById('btn-execute-recovery');
    const requestBtn = document.getElementById('btn-request-recovery');

    if (confirmCheck && executeBtn) {
      confirmCheck.addEventListener('change', () => {
        executeBtn.disabled = !confirmCheck.checked;
      });
    }

    if (executeBtn) {
      executeBtn.addEventListener('click', () => this.executeRecovery());
    }
    if (requestBtn) {
      requestBtn.addEventListener('click', () => this.executeRecovery());
    }
  }

  renderRecovery(data) {
    const phase = (data.phase || 'UNKNOWN').toUpperCase();
    const safeState = (data.safe_state || 'unknown').toLowerCase();
    const isBlocked =
      data.status !== 'ok' ||
      phase !== 'READY' ||
      safeState === 'unknown' ||
      safeState === 'motion_blocked' ||
      safeState === 'blocked';
    const badge = document.getElementById('recovery-state-badge');
    const reasonDisplay = document.getElementById('freeze-reason-display');
    const evidenceDisplay = document.getElementById('freeze-evidence-display');
    const blockersList = document.getElementById('recovery-blockers-list');

    if (badge) {
      badge.textContent = isBlocked ? '准入阻断/未知' : 'Gateway 未报告阻断';
      badge.className = isBlocked ? 'badge badge-danger' : 'badge badge-warning';
    }

    if (reasonDisplay) {
      if (isBlocked) {
        reasonDisplay.innerHTML = `
          <div class="freeze-status-text text-danger">
            <span class="freeze-icon">🚨</span>
            <span class="freeze-message"><strong>准入阻断或未知：</strong>${this.escapeHtml(data.summary || 'Gateway 未提供可用准入结论；未收到物理停止证据。')}</span>
          </div>
        `;
      } else {
        reasonDisplay.innerHTML = `
          <div class="freeze-status-text">
            <span class="freeze-icon">ℹ️</span>
            <span class="freeze-message">Gateway 当前未报告准入阻断；这不证明传感器、急停或底盘处于安全状态。</span>
          </div>
        `;
      }
    }

    if (evidenceDisplay) {
      if (isBlocked && data.doctor_snapshot) {
        evidenceDisplay.innerHTML = `
          <div class="evidence-box">
            <pre class="evidence-pre">${this.escapeHtml(JSON.stringify(data.doctor_snapshot, null, 2))}</pre>
          </div>
        `;
      } else {
        evidenceDisplay.innerHTML = `
          <div class="evidence-empty">暂无冻结证据快照。当触发安全准入拦截时，此处将展示阻断传感器数据。</div>
        `;
      }
    }

    if (blockersList) {
      if (isBlocked) {
        blockersList.innerHTML = `
          <li class="blocker-item blocked">
            <span class="blocker-icon">⚠️</span>
            <span class="blocker-text">${this.escapeHtml(data.summary || '硬件或逻辑安全门限未满足')}</span>
          </li>
        `;
      } else {
        blockersList.innerHTML = `
          <li class="blocker-item">
            <span class="blocker-icon">?</span>
            <span class="blocker-text">Gateway 未报告当前阻断项；具体硬件状态仍为 UNKNOWN。</span>
          </li>
        `;
      }
    }
  }

  async executeRecovery() {
    const confirmCheck = document.getElementById('recovery-confirm-check');
    if (!confirmCheck || !confirmCheck.checked) {
      this.showToast('请先勾选操作员现场物理安全核实确认项', 'warning');
      return;
    }

    const reasonInput = document.getElementById('recovery-reason');
    const reason = (reasonInput ? reasonInput.value : '').trim()
      || '操作员已勾选现场核实项；未提供补充说明';

    const executeBtn = document.getElementById('btn-execute-recovery');
    if (executeBtn) executeBtn.disabled = true;

    try {
      const response = await fetch('/recover', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify({
          operator_confirmed: true,
          reason,
          robot_id: this.state.robot.activeRobotId,
        }),
      });

      if (!response.ok) {
        await this.handleApiError(response, 'safety_blocked');
        return;
      }

      await response.json();
      this.showToast(
        'Gateway 已处理准入投影重置请求；机器人物理状态仍为 UNKNOWN，正在重新读取 readiness',
        'info',
      );

      // Clear input & checkbox
      if (confirmCheck) confirmCheck.checked = false;
      if (reasonInput) reasonInput.value = '';

      // Refresh readiness
      await this.fetchReadiness();
    } catch (err) {
      console.error('Failed to execute recovery:', err);
      await this.handleApiError(err, 'safety_blocked');
    } finally {
      if (executeBtn && confirmCheck) {
        executeBtn.disabled = !confirmCheck.checked;
      }
    }
  }

  // -------------------------------------------------------------------------
  // Page 5: Experimental Settings Assistant & Diagnostics
  // -------------------------------------------------------------------------

  bindSettingsEvents() {
    // Template & Discovery events
    const applyTemplateBtn = document.getElementById('btn-apply-template');
    if (applyTemplateBtn) {
      applyTemplateBtn.addEventListener('click', () => this.applySelectedTemplate());
    }

    const templateSelect = document.getElementById('template-select');
    if (templateSelect) {
      templateSelect.addEventListener('change', (e) => this.applySelectedTemplate(e.target.value));
    }

    const discoverRosBtn = document.getElementById('btn-discover-ros');
    if (discoverRosBtn) {
      discoverRosBtn.addEventListener('click', () => this.discoverRosTopics());
    }

    // Schema Diff & Save events
    const previewDiffBtn = document.getElementById('btn-preview-diff');
    if (previewDiffBtn) {
      previewDiffBtn.addEventListener('click', () => this.openDiffModal());
    }

    // Snapshot Rollback events
    const rollbackBtn = document.getElementById('btn-rollback-snapshot');
    if (rollbackBtn) {
      rollbackBtn.addEventListener('click', () => this.rollbackSnapshot());
    }

    // Diagnostic Connectivity Test events
    const testRosBtn = document.getElementById('btn-test-ros');
    if (testRosBtn) {
      testRosBtn.addEventListener('click', () => this.testRosConnectivity());
    }

    const testGwBtn = document.getElementById('btn-test-gateway');
    if (testGwBtn) {
      testGwBtn.addEventListener('click', () => this.testGatewayConnectivity());
    }
  }

  async loadTemplates() {
    const select = document.getElementById('template-select');
    try {
      const resp = await fetch('/config/templates');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      const templates = data.templates || [];
      this.state.config.templates = templates;

      if (select && templates.length > 0) {
        select.innerHTML = '';
        templates.forEach((t) => {
          const opt = document.createElement('option');
          opt.value = t.template_id;
          opt.textContent = `${t.name_zh} (${t.mode === 'real' ? '实机' : '仿真'})`;
          select.appendChild(opt);
        });
      }
      return templates;
    } catch (err) {
      console.warn('Failed to load robot templates:', err);
    }
  }

  applySelectedTemplate(templateId = null) {
    const select = document.getElementById('template-select');
    const targetId = templateId || (select ? select.value : '');
    const templates = this.state.config?.templates || [];
    const template = templates.find((t) => t.template_id === targetId) || {
      template_id: targetId,
      name_zh: targetId,
      recommended_topics: {
        laser_scan_topic: '/scan',
        odometry_topic: '/odom',
        cmd_vel_topic: '/cmd_vel',
        rgb_camera_topic: '/camera/image_raw',
        thermal_camera_topic: '/thermal/image_raw',
        gas_sensor_topic: '/sensors/gas',
      },
    };

    const topics = template.recommended_topics || {};
    for (const [topicKey, topicVal] of Object.entries(topics)) {
      const input = document.getElementById(`field-${topicKey}`) || document.querySelector(`input[data-field="${topicKey}"]`);
      if (input) {
        input.value = topicVal;
      }
    }

    const logBox = document.getElementById('settings-log-box');
    const now = new Date().toLocaleTimeString();
    if (logBox) {
      logBox.textContent += `\n[${now}] [TEMPLATE_APPLIED] 已成功加载预设模板: ${template.name_zh || targetId}，预填 ${Object.keys(topics).length} 个推荐话题。`;
      logBox.scrollTop = logBox.scrollHeight;
    }
    this.showToast(`已应用预设模板: ${template.name_zh || targetId}`, 'success');
  }

  async discoverRosTopics() {
    const logBox = document.getElementById('settings-log-box');
    const now = new Date().toLocaleTimeString();

    if (logBox) {
      logBox.textContent += `\n[${now}] [DISCOVER] 正在探测 ROS 计算图与活跃传感器话题...`;
      logBox.scrollTop = logBox.scrollHeight;
    }

    try {
      const resp = await fetch('/config/discover', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ timeout: 2.0 }),
      });
      const report = await resp.json();

      const suggested = report.suggested_rules || [];
      let mappedCount = 0;
      suggested.forEach((rule) => {
        const topic = rule.topic_pattern || '';
        const sensor = rule.sensor || '';
        let targetField = null;
        if (sensor === 'lidar' || topic.includes('scan')) targetField = 'laser_scan_topic';
        else if (sensor === 'thermal_camera' || topic.includes('thermal')) targetField = 'thermal_camera_topic';
        else if (sensor === 'rgb_camera' || topic.includes('camera') || topic.includes('image')) targetField = 'rgb_camera_topic';
        else if (sensor === 'gas_detector' || topic.includes('gas')) targetField = 'gas_sensor_topic';
        else if (topic.includes('odom')) targetField = 'odometry_topic';
        else if (topic.includes('cmd_vel')) targetField = 'cmd_vel_topic';

        if (targetField) {
          const input = document.getElementById(`field-${targetField}`) || document.querySelector(`input[data-field="${targetField}"]`);
          if (input) {
            input.value = topic;
            mappedCount++;
          }
        }
      });

      if (logBox) {
        logBox.textContent += `\n[${now}] [DISCOVER_SUCCESS] ROS 计算图探测完成: 发现 ${report.discovered_topics ? report.discovered_topics.length : 0} 个话题，自动映射 ${mappedCount} 个核心传感器字段。`;
        logBox.scrollTop = logBox.scrollHeight;
      }
      this.showToast(`ROS 计算图自动探测完成 (匹配 ${mappedCount} 个字段)`, 'success');
      return report;
    } catch (err) {
      if (logBox) {
        logBox.textContent += `\n[${now}] [DISCOVER_WARN] ROS 计算图探测异常: ${err.message}`;
        logBox.scrollTop = logBox.scrollHeight;
      }
      this.showToast(`ROS 探测失败: ${err.message}`, 'warning');
    }
  }

  async renderDynamicSchemaForm() {
    const container = document.getElementById('schema-fields-container');
    if (!container) return;

    try {
      const resp = await fetch('/config/schema');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      const schemas = data.schemas || {};
      this.state.config.schemas = schemas;

      // Filter unique schema objects by plugin_name
      const uniqueSchemas = {};
      for (const [k, s] of Object.entries(schemas)) {
        if (s && s.plugin_name && !uniqueSchemas[s.plugin_name]) {
          uniqueSchemas[s.plugin_name] = s;
        }
      }

      container.innerHTML = '';
      const schemaList = Object.values(uniqueSchemas);
      if (schemaList.length === 0) {
        container.innerHTML = '<div class="schema-loading-placeholder">暂无可配置插件 Schema 规范。</div>';
        return;
      }

      schemaList.forEach((schema) => {
        (schema.fields || []).forEach((fieldDef) => {
          const card = document.createElement('div');
          card.className = 'schema-field-card';
          const fieldType = fieldDef.type || 'string';
          const isSecret = Boolean(fieldDef.secret);
          const inputType = (fieldType === 'integer' || fieldType === 'float') ? 'number' : isSecret ? 'password' : 'text';
          const defaultVal = fieldDef.default !== undefined && fieldDef.default !== null ? fieldDef.default : '';

          card.innerHTML = `
            <div class="schema-field-header">
              <div class="field-title-group">
                <span class="field-label-zh">${this.escapeHtml(fieldDef.label_zh || fieldDef.name)}</span>
                <span class="field-name-tag">${this.escapeHtml(fieldDef.name)}</span>
                ${fieldDef.required ? '<span class="badge badge-info">必填</span>' : ''}
              </div>
              <span class="badge badge-secondary">${this.escapeHtml(schema.plugin_name.split('.').pop())}</span>
            </div>
            <p class="field-desc">${this.escapeHtml(fieldDef.description_zh || '')}</p>
            <div class="field-input-probe-group">
              <input type="${inputType}" class="field-input" id="field-${fieldDef.name}" data-field="${fieldDef.name}" data-probe="${fieldDef.probe_action || ''}" value="${this.escapeHtml(String(defaultVal))}" placeholder="示例: ${this.escapeHtml(fieldDef.example || '')}">
              <button type="button" class="btn-field-probe" id="btn-test-${fieldDef.name}" data-field="${fieldDef.name}" data-probe="${fieldDef.probe_action || ''}">测试连接</button>
            </div>
            <div class="field-probe-result" id="probe-result-${fieldDef.name}"></div>
            <div class="field-example-hint">示例: ${this.escapeHtml(fieldDef.example || '')}</div>
          `;

          container.appendChild(card);

          // Wire probe button event
          const probeBtn = card.querySelector(`#btn-test-${fieldDef.name}`);
          if (probeBtn) {
            probeBtn.addEventListener('click', () => {
              const input = card.querySelector(`#field-${fieldDef.name}`);
              this.testField(fieldDef.name, input ? input.value : '', fieldDef.probe_action);
            });
          }
        });
      });
    } catch (err) {
      console.warn('Failed to render dynamic schema form:', err);
      container.innerHTML = `<div class="schema-loading-placeholder text-danger">加载 Schema 失败: ${this.escapeHtml(err.message)}</div>`;
    }
  }

  async testField(fieldName, val = null, probe = null) {
    const inputEl = document.getElementById(`field-${fieldName}`) || document.querySelector(`input[data-field="${fieldName}"]`);
    const fieldValue = val !== null ? String(val) : (inputEl ? inputEl.value : '');
    const probeAction = probe || (inputEl ? inputEl.dataset.probe : '') || '';
    const resultEl = document.getElementById(`probe-result-${fieldName}`);
    const logBox = document.getElementById('settings-log-box');
    const now = new Date().toLocaleTimeString();

    if (resultEl) {
      resultEl.className = 'field-probe-result';
      resultEl.innerHTML = `⏳ 正在探测 ${fieldName} (${fieldValue})...`;
    }
    if (logBox) {
      logBox.textContent += `\n[${now}] [PROBE] 正在探测字段 ${fieldName} 连通性 (值: ${fieldValue})...`;
      logBox.scrollTop = logBox.scrollHeight;
    }

    try {
      const resp = await fetch('/config/test-field', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          field_name: fieldName,
          field_value: fieldValue,
          probe_action: probeAction,
        }),
      });
      const data = await resp.json();
      if (data.status === 'ok') {
        if (resultEl) {
          resultEl.className = 'field-probe-result success';
          resultEl.textContent = `✔ 连通正常 (${data.latency_ms}ms) - ${data.message}`;
        }
        if (logBox) {
          logBox.textContent += `\n[${now}] [PROBE_SUCCESS] 字段 ${fieldName} 连通正常 (${data.latency_ms}ms): ${data.message}`;
          logBox.scrollTop = logBox.scrollHeight;
        }
        this.showToast(`字段 ${fieldName} 连通测试通过`, 'success');
      } else {
        if (resultEl) {
          resultEl.className = 'field-probe-result error';
          resultEl.textContent = `✖ 探测异常 (${data.latency_ms || 0}ms) - ${data.message}`;
        }
        if (logBox) {
          logBox.textContent += `\n[${now}] [PROBE_WARN] 字段 ${fieldName} 探测失败: ${data.message}`;
          logBox.scrollTop = logBox.scrollHeight;
        }
        this.showToast(`字段 ${fieldName} 探测失败: ${data.message}`, 'warning');
      }
      return data;
    } catch (err) {
      if (resultEl) {
        resultEl.className = 'field-probe-result error';
        resultEl.textContent = `✖ 接口请求失败: ${err.message}`;
      }
      if (logBox) {
        logBox.textContent += `\n[${now}] [PROBE_ERROR] 字段 ${fieldName} 请求失败: ${err.message}`;
        logBox.scrollTop = logBox.scrollHeight;
      }
      this.showToast(`连通性测试请求异常: ${err.message}`, 'danger');
    }
  }

  async openDiffModal() {
    const modal = document.getElementById('diff-modal');
    const alertsContainer = document.getElementById('diff-impact-alerts');
    const viewerContainer = document.getElementById('diff-viewer');
    if (!modal) return;

    if (!this.state.profilePath || !this.state.config.currentProfileToml) {
      this.showToast(
        'Gateway 尚未提供 active Profile 及其原文；禁止用推测配置生成或保存差异',
        'warning',
      );
      return;
    }

    if (alertsContainer) alertsContainer.innerHTML = '<div class="diff-placeholder">正在评估配置影响级别...</div>';
    if (viewerContainer) viewerContainer.innerHTML = '<div class="diff-placeholder">正在计算配置差异...</div>';
    modal.style.display = 'flex';

    // Gather current values from dynamic schema form
    const currentValues = {};
    document.querySelectorAll('#schema-fields-container .field-input').forEach((input) => {
      const fieldName = input.dataset.field;
      if (fieldName) {
        currentValues[fieldName] = input.value;
      }
    });

    const oldConfig = this.state.config.currentProfileToml;

    const robotId = currentValues.robot_id || this.state.robot.activeRobotId || 'UNKNOWN';
    const runtimeMode = currentValues.mode || this.state.robot.mode || 'unknown';
    let newToml = `[robot]\nid = "${robotId}"\nbase_url = "${currentValues.base_url || this.state.gatewayUrl}"\nmode = "${runtimeMode}"\n`;
    for (const [k, v] of Object.entries(currentValues)) {
      if (k !== 'robot_id' && k !== 'base_url' && k !== 'mode') {
        newToml += `${k} = "${v}"\n`;
      }
    }
    this.state.config.pendingToml = newToml;

    try {
      const resp = await fetch('/config/diff', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          old_config: oldConfig,
          new_config: newToml,
        }),
      });
      const data = await resp.json();
      this.renderDiffModalContent(data, oldConfig, newToml);
    } catch (err) {
      if (alertsContainer) {
        alertsContainer.innerHTML = `<div class="impact-item impact-warning"><span class="badge badge-warning">WARN</span><span class="impact-text">Diff 计算失败: ${this.escapeHtml(err.message)}</span></div>`;
      }
      if (viewerContainer) {
        viewerContainer.innerHTML = `<div class="diff-placeholder text-danger">无法获取差异数据: ${this.escapeHtml(err.message)}</div>`;
      }
    }
  }

  renderDiffModalContent(diffData, oldToml, newToml) {
    const alertsContainer = document.getElementById('diff-impact-alerts');
    const viewerContainer = document.getElementById('diff-viewer');

    if (alertsContainer) {
      alertsContainer.innerHTML = '';
      const impactItems = diffData.diff_fields || diffData.impact_items || [];
      if (impactItems.length === 0) {
        alertsContainer.innerHTML = `
          <div class="impact-item impact-info">
            <span class="impact-badge badge badge-info">INFO</span>
            <span class="impact-text">当前 diff 规则未报告影响项；这不证明配置可启动或满足实机安全准入。</span>
          </div>
        `;
      } else {
        impactItems.forEach((item) => {
          const level = (item.impact_level || item.level || 'info').toLowerCase();
          const badgeClass = level === 'critical' ? 'badge badge-danger' : level === 'warning' ? 'badge badge-warning' : 'badge badge-info';
          const itemClass = level === 'critical' ? 'impact-item impact-critical' : level === 'warning' ? 'impact-item impact-warning' : 'impact-item impact-info';
          const desc = item.impact_description_zh || item.message || item.description || `字段 ${item.path || ''} (${item.change_type || '修改'})`;
          const div = document.createElement('div');
          div.className = itemClass;
          div.innerHTML = `
            <span class="impact-badge ${badgeClass}">${level.toUpperCase()}</span>
            <span class="impact-text"><strong>${this.escapeHtml(item.path || '')}:</strong> ${this.escapeHtml(desc)}</span>
          `;
          alertsContainer.appendChild(div);
        });
      }
    }

    if (viewerContainer) {
      viewerContainer.innerHTML = '';
      const oldLines = (oldToml || '').split('\n');
      const newLines = (newToml || '').split('\n');
      const maxLines = Math.max(oldLines.length, newLines.length);

      for (let i = 0; i < maxLines; i++) {
        const o = oldLines[i] !== undefined ? oldLines[i] : '';
        const n = newLines[i] !== undefined ? newLines[i] : '';
        const lineDiv = document.createElement('div');

        if (o === n) {
          lineDiv.className = 'diff-line diff-line-same';
          lineDiv.innerHTML = `<span class="diff-line-num">${i + 1}</span><span class="diff-line-code">  ${this.escapeHtml(n)}</span>`;
        } else if (!o && n) {
          lineDiv.className = 'diff-line diff-line-add';
          lineDiv.innerHTML = `<span class="diff-line-num">${i + 1}</span><span class="diff-line-code">+ ${this.escapeHtml(n)}</span>`;
        } else if (o && !n) {
          lineDiv.className = 'diff-line diff-line-del';
          lineDiv.innerHTML = `<span class="diff-line-num">${i + 1}</span><span class="diff-line-code">- ${this.escapeHtml(o)}</span>`;
        } else {
          lineDiv.className = 'diff-line diff-line-add';
          lineDiv.innerHTML = `<span class="diff-line-num">${i + 1}</span><span class="diff-line-code">~ ${this.escapeHtml(n)} (原: ${this.escapeHtml(o)})</span>`;
        }
        viewerContainer.appendChild(lineDiv);
      }
    }
  }

  closeDiffModal() {
    const modal = document.getElementById('diff-modal');
    if (modal) modal.style.display = 'none';
  }

  async saveProfile() {
    const content = this.state.config.pendingToml;
    const profilePath = this.state.profilePath;
    const logBox = document.getElementById('settings-log-box');
    const now = new Date().toLocaleTimeString();

    if (!profilePath || !content) {
      this.showToast(
        '缺少 Gateway 提供的 active Profile 或待保存内容；未写入任何配置',
        'warning',
      );
      return;
    }

    try {
      const resp = await fetch('/config/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          profile_path: profilePath,
          content: content,
          reason: 'Saved via FireClaw Web Console',
          create_snapshot: true,
        }),
      });
      const data = await resp.json();
      if (resp.ok) {
        this.closeDiffModal();
        this.state.config.currentProfileToml = content;
        this.showToast(
          `配置写入请求成功，快照 ID: ${data.snapshot_id || '未返回'}；尚未证明该 Profile 可启动`,
          'info',
        );
        if (logBox) {
          logBox.textContent += `\n[${now}] [CONFIG_SAVED] 配置已写入 ${profilePath}，生成内容快照: ${data.snapshot_id}`;
          logBox.scrollTop = logBox.scrollHeight;
        }
        await this.loadSnapshots();
        return data;
      } else {
        await this.handleApiError(resp, 'config_save_failed');
      }
    } catch (err) {
      await this.handleApiError(err, 'config_save_failed');
      if (logBox) {
        logBox.textContent += `\n[${now}] [CONFIG_ERROR] 保存配置失败: ${err.message}`;
        logBox.scrollTop = logBox.scrollHeight;
      }
    }
  }

  async loadSnapshots() {
    const select = document.getElementById('snapshot-select');
    const profilePath = this.state.profilePath;
    if (!profilePath) {
      if (select) {
        select.innerHTML = '<option value="">Gateway 尚未提供 active Profile</option>';
      }
      return [];
    }
    try {
      const resp = await fetch(`/config/history?profile=${encodeURIComponent(profilePath)}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      const snapshots = data.snapshots || [];
      this.state.config.snapshots = snapshots;

      if (select) {
        select.innerHTML = '';
        if (snapshots.length === 0) {
          select.innerHTML = '<option value="">暂无历史快照记录</option>';
        } else {
          snapshots.forEach((s) => {
            const opt = document.createElement('option');
            opt.value = s.snapshot_id;
            const shortId = (s.snapshot_id || '').substring(0, 8);
            opt.textContent = `[${s.created_at || s.timestamp || 'SNAPSHOT'}] ${shortId} - ${s.summary || '快照'}`;
            select.appendChild(opt);
          });
        }
      }
      return snapshots;
    } catch (err) {
      if (select) {
        select.innerHTML = '<option value="">无法加载快照历史</option>';
      }
    }
  }

  async rollbackSnapshot() {
    const select = document.getElementById('snapshot-select');
    const snapshotId = select ? select.value : '';
    const profilePath = this.state.profilePath;
    const logBox = document.getElementById('settings-log-box');
    const now = new Date().toLocaleTimeString();

    if (!profilePath) {
      this.showToast('Gateway 尚未提供 active Profile；禁止推测回滚目标', 'warning');
      return;
    }

    if (!snapshotId) {
      this.showToast('请先选择要回滚的历史快照', 'warning');
      return;
    }

    try {
      const resp = await fetch('/config/rollback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          snapshot_id: snapshotId,
          profile_path: profilePath,
        }),
      });
      const data = await resp.json();
      if (resp.ok) {
        this.showToast(
          `已恢复快照内容: ${snapshotId.substring(0, 8)}；尚未验证该 Profile 可启动`,
          'info',
        );
        if (logBox) {
          logBox.textContent += `\n[${now}] [ROLLBACK_SUCCESS] 已成功回滚至快照: ${snapshotId} (${data.message || 'OK'})`;
          logBox.scrollTop = logBox.scrollHeight;
        }
        await this.loadSnapshots();
        await this.renderDynamicSchemaForm();
        return data;
      } else {
        await this.handleApiError(resp, 'config_rollback_failed');
      }
    } catch (err) {
      await this.handleApiError(err, 'config_rollback_failed');
      if (logBox) {
        logBox.textContent += `\n[${now}] [ROLLBACK_ERROR] 回滚失败: ${err.message}`;
        logBox.scrollTop = logBox.scrollHeight;
      }
    }
  }

  async testRosConnectivity() {
    const logBox = document.getElementById('settings-log-box');
    const badge = document.getElementById('settings-log-badge');
    const now = new Date().toLocaleTimeString();

    if (logBox) logBox.textContent += `\n[${now}] [DIAG] 正在探测 ROS 1 Master 与底盘通信...`;

    try {
      const resp = await fetch('/fleet/doctor');
      if (resp.ok) {
        const data = await resp.json();
        if (logBox) {
          logBox.textContent += `\n[${now}] [DIAG_SUCCESS] ROS 节点通信正常: ${JSON.stringify(data.findings ? data.findings.length : '0')} 项诊断结果。`;
          logBox.scrollTop = logBox.scrollHeight;
        }
        if (badge) {
          badge.textContent = 'ROS OK';
          badge.className = 'badge badge-success';
        }
        this.showToast('ROS 通信诊断检测通过', 'success');
      } else {
        await this.handleApiError(resp, 'ros_master_unreachable');
      }
    } catch (err) {
      if (logBox) {
        logBox.textContent += `\n[${now}] [DIAG_WARN] ROS 通信异常: ${err.message}`;
        logBox.scrollTop = logBox.scrollHeight;
      }
      if (badge) {
        badge.textContent = 'ROS WARN';
        badge.className = 'badge badge-warning';
      }
      await this.handleApiError(err, 'ros_master_unreachable');
    }
  }

  async testGatewayConnectivity() {
    const logBox = document.getElementById('settings-log-box');
    const badge = document.getElementById('settings-log-badge');
    const now = new Date().toLocaleTimeString();

    if (logBox) logBox.textContent += `\n[${now}] [DIAG] 正在测试 Gateway HTTP 与 SSE 网关接口...`;

    try {
      const resp = await fetch('/health');
      if (resp.ok) {
        const data = await resp.json();
        if (logBox) {
          logBox.textContent += `\n[${now}] [DIAG_SUCCESS] Gateway 网关服务运行正常 (Status: ${data.status})。`;
          logBox.scrollTop = logBox.scrollHeight;
        }
        if (badge) {
          badge.textContent = 'GW OK';
          badge.className = 'badge badge-success';
        }
        this.showToast('Gateway 连通性测试正常', 'success');
      } else {
        await this.handleApiError(resp, 'gateway_connection_failed');
      }
    } catch (err) {
      if (logBox) {
        logBox.textContent += `\n[${now}] [DIAG_ERROR] Gateway 连接失败: ${err.message}`;
        logBox.scrollTop = logBox.scrollHeight;
      }
      if (badge) {
        badge.textContent = 'GW ERR';
        badge.className = 'badge badge-danger';
      }
      await this.handleApiError(err, 'gateway_connection_failed');
    }
  }

  // -------------------------------------------------------------------------
  // Modals (4-Part Error Modal & Diff Modal)
  // -------------------------------------------------------------------------

  bindModalEvents() {
    // 4-part error modal
    const modal = document.getElementById('error-modal');
    const closeBtn = document.getElementById('btn-modal-close');
    const dismissBtn = document.getElementById('btn-modal-dismiss');
    const goRecoveryBtn = document.getElementById('btn-modal-go-recovery');

    if (closeBtn) closeBtn.addEventListener('click', () => this.closeErrorModal());
    if (dismissBtn) dismissBtn.addEventListener('click', () => this.closeErrorModal());

    if (goRecoveryBtn) {
      goRecoveryBtn.addEventListener('click', () => {
        this.closeErrorModal();
        this.switchTab('recovery');
      });
    }

    if (modal) {
      modal.addEventListener('click', (e) => {
        if (e.target === modal) {
          this.closeErrorModal();
        }
      });
    }

    // Diff modal events
    const diffModal = document.getElementById('diff-modal');
    const cancelDiffBtn = document.getElementById('btn-cancel-diff');
    const modalCancelDiffBtn = document.getElementById('btn-modal-cancel-diff');
    const confirmSaveBtn = document.getElementById('btn-confirm-save-profile');

    if (cancelDiffBtn) cancelDiffBtn.addEventListener('click', () => this.closeDiffModal());
    if (modalCancelDiffBtn) modalCancelDiffBtn.addEventListener('click', () => this.closeDiffModal());
    if (confirmSaveBtn) confirmSaveBtn.addEventListener('click', () => this.saveProfile());

    if (diffModal) {
      diffModal.addEventListener('click', (e) => {
        if (e.target === diffModal) {
          this.closeDiffModal();
        }
      });
    }
  }

  // -------------------------------------------------------------------------
  // Friendly Errors & Structured 4-Part Modals (Task 3)
  // -------------------------------------------------------------------------

  /**
   * Route structured error responses by severity:
   * - 'critical' -> Full 4-part error modal with suggested action buttons & technical details
   * - 'warning'  -> 2-line enhanced toast with safety status and "查看详情 →" link
   * - 'info'     -> Standard compact toast notification
   */
  showFriendlyError(errorData) {
    if (!errorData) return;
    const data = typeof errorData === 'string' ? { what_happened: errorData } : { ...errorData };

    const severity = (data.severity || 'warning').toLowerCase();
    const whatHappened = data.what_happened || data.message || data.error_code || data.code || '未预期系统异常';
    const safeStatus = data.robot_safe_status || '未收到可验证的机器人安全状态，请按状态未知处理';

    if (severity === 'critical') {
      this.showErrorModal(data);
    } else if (severity === 'warning') {
      this.showEnhancedToast(whatHappened, safeStatus, data);
    } else {
      this.showToast(whatHappened, 'info');
    }
  }

  /**
   * Show enhanced 2-line toast notification with safe status and "查看详情 →" link.
   * Extended to 8000ms duration to allow reading time in field conditions.
   */
  showEnhancedToast(message, safeStatus, errorData = null, duration = 8000) {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = 'toast-item toast-enhanced';
    toast.innerHTML = `
      <div class="toast-enhanced-header">
        <span class="toast-icon">⚠️</span>
        <span class="toast-msg">${this.escapeHtml(message)}</span>
      </div>
      <div class="toast-enhanced-body">
        <span class="toast-enhanced-safe-status">${this.escapeHtml(safeStatus || '未收到可验证的机器人安全状态，请按状态未知处理')}</span>
        <a href="javascript:void(0)" class="toast-enhanced-link">查看详情 →</a>
      </div>
    `;

    const link = toast.querySelector('.toast-enhanced-link');
    if (link) {
      link.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.showErrorModal(errorData || {
          what_happened: message,
          robot_safe_status: safeStatus || '安全状态未知；当前界面没有物理停止证据。',
          action_taken: '界面已停止继续提交当前请求；未推断机器人已停止。',
          next_steps: '请查看机器人运行时证据；必要时在现场执行急停。',
        });
        toast.remove();
      });
    }

    container.appendChild(toast);

    setTimeout(() => {
      toast.classList.add('fade-out');
      setTimeout(() => toast.remove(), 300);
    }, duration);
  }

  /**
   * Enhanced 4-Part Structured Error Modal.
   * Backward compatible with both:
   * 1. showErrorModal(errorDataObject)
   * 2. showErrorModal(title, whatHappened, robotSafeStatus, actionTaken, nextSteps)
   */
  showErrorModal(titleOrData, whatHappened, robotSafeStatus, actionTaken, nextSteps) {
    const modal = document.getElementById('error-modal');
    if (!modal) return;

    let title, what, safe, action, next, suggestedActions, techDetails;

    if (typeof titleOrData === 'object' && titleOrData !== null) {
      const data = titleOrData;
      const isCritical = (data.severity || '').toLowerCase() === 'critical';
      title = data.title || (isCritical ? 'FireClaw 严重故障处置' : 'FireClaw 安全警告与故障处置');
      what = data.what_happened || data.message || '未预期异常事件。';
      safe = data.robot_safe_status || '安全状态未知；当前响应没有提供物理停止证据。';
      action = data.action_taken || '已停止继续处理当前界面请求；未推断机器人已停止。';
      next = data.next_steps || '请查看机器人运行时证据；必要时在现场执行急停。';
      suggestedActions = data.suggested_actions || [];
      techDetails = data.technical_details || '';
    } else {
      title = titleOrData || 'FireClaw 安全警告与故障处置';
      what = whatHappened || '未预期异常事件。';
      safe = robotSafeStatus || '安全状态未知；当前响应没有提供物理停止证据。';
      action = actionTaken || '已停止继续处理当前界面请求；未推断机器人已停止。';
      next = nextSteps || '请查看机器人运行时证据；必要时在现场执行急停。';
      suggestedActions = [];
      techDetails = '';
    }

    const titleEl = document.getElementById('modal-title');
    const whatEl = document.querySelector('#modal-what-happened .quad-text');
    const safeEl = document.querySelector('#modal-robot-safe-status .quad-text');
    const actionEl = document.querySelector('#modal-action-taken .quad-text');
    const nextEl = document.querySelector('#modal-next-steps .quad-text');

    if (titleEl) titleEl.textContent = title;
    if (whatEl) whatEl.textContent = what;
    if (safeEl) safeEl.textContent = safe;
    if (actionEl) actionEl.textContent = action;
    if (nextEl) nextEl.textContent = next;

    // Render dynamic suggested action buttons
    const actionsContainer = document.getElementById('modal-suggested-actions');
    if (actionsContainer) {
      actionsContainer.innerHTML = '';
      if (Array.isArray(suggestedActions) && suggestedActions.length > 0) {
        actionsContainer.style.display = 'flex';
        suggestedActions.forEach((act) => {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'btn btn-suggested-action';
          const label = typeof act === 'object' ? (act.label || act.action || '执行处置') : String(act);
          const actCode = typeof act === 'object' ? act.action : String(act);
          btn.textContent = label;

          btn.addEventListener('click', () => {
            if (actCode === 'recovery' || actCode === 'goto_recovery') {
              this.closeErrorModal();
              this.switchTab('recovery');
            } else if (actCode === 'test_ros') {
              this.closeErrorModal();
              this.switchTab('settings');
              this.testRosConnectivity();
            } else if (actCode === 'test_gateway') {
              this.closeErrorModal();
              this.switchTab('settings');
              this.testGatewayConnectivity();
            } else if (actCode === 'retry_discover') {
              this.closeErrorModal();
              this.switchTab('settings');
              this.discoverRosTopics();
            } else if (typeof act === 'object' && typeof act.callback === 'function') {
              act.callback();
              this.closeErrorModal();
            } else {
              this.closeErrorModal();
            }
          });

          actionsContainer.appendChild(btn);
        });
      } else {
        actionsContainer.style.display = 'none';
      }
    }

    // Render collapsible technical details
    const techDetailsPanel = document.getElementById('modal-technical-details');
    const techContentEl = document.getElementById('modal-tech-content');
    if (techDetailsPanel && techContentEl) {
      if (techDetails) {
        techContentEl.textContent = typeof techDetails === 'object' ? JSON.stringify(techDetails, null, 2) : String(techDetails);
        techDetailsPanel.open = false; // default to closed
        techDetailsPanel.style.display = 'block';
      } else {
        techContentEl.textContent = '';
        techDetailsPanel.open = false;
        techDetailsPanel.style.display = 'none';
      }
    }

    modal.style.display = 'flex';
  }

  closeErrorModal() {
    const modal = document.getElementById('error-modal');
    if (modal) modal.style.display = 'none';
  }

  /**
   * Global Error Handler:
   * Parses Gateway structured `{"error": {...}}` JSON payload and routes to showFriendlyError.
   * Supports Response objects, Error exceptions, or raw error objects.
   */
  async handleApiError(respOrErr, fallbackCode = null) {
    try {
      if (respOrErr && typeof respOrErr.json === 'function') {
        let data = {};
        try {
          data = await respOrErr.json();
        } catch (_) {
          data = {};
        }

        if (data && data.error && typeof data.error === 'object') {
          this.showFriendlyError(data.error);
          return data.error;
        }

        const msg = (data && (data.message || data.detail)) || `HTTP ${respOrErr.status} ${respOrErr.statusText || ''}`.trim();
        const errObj = {
          error_code: fallbackCode || 'gateway_request_failed',
          severity: respOrErr.status >= 500 ? 'critical' : 'warning',
          what_happened: msg,
          robot_safe_status: '网关不可达，无法确认机器人当前物理状态',
          action_taken: '已中止当前界面请求；未推断底盘已停止',
          next_steps: '请检查网关与机器人运行时；必要时在现场执行急停。',
          technical_details: `${respOrErr.url || ''} (HTTP ${respOrErr.status})`,
        };
        this.showFriendlyError(errObj);
        return errObj;
      }

      if (respOrErr instanceof Error) {
        const errObj = {
          error_code: fallbackCode || 'network_disconnect',
          severity: 'warning',
          what_happened: respOrErr.message || '网络连接异常或网关无响应',
          robot_safe_status: '通信中断，无法确认机器人当前物理状态',
          action_taken: '已终止界面通信重试；未推断底盘已停止',
          next_steps: '请核实 Gateway 与 ROS 通信；必要时在现场执行急停。',
          technical_details: respOrErr.stack || String(respOrErr),
        };
        this.showFriendlyError(errObj);
        return errObj;
      }

      if (respOrErr && typeof respOrErr === 'object') {
        this.showFriendlyError(respOrErr);
        return respOrErr;
      }

      const errObj = {
        error_code: fallbackCode || 'system_error',
        severity: 'warning',
        what_happened: String(respOrErr || '系统出现未预期异常'),
        robot_safe_status: '当前错误没有附带可验证的机器人安全状态',
        action_taken: '已停止继续处理当前界面请求；未推断机器人已停止',
        next_steps: '请查看技术详情和机器人运行时证据；必要时在现场执行急停。',
      };
      this.showFriendlyError(errObj);
      return errObj;
    } catch (handlerErr) {
      console.error('Error within handleApiError:', handlerErr);
      this.showToast(`错误: ${String(respOrErr && respOrErr.message ? respOrErr.message : respOrErr)}`, 'danger');
    }
  }

  // -------------------------------------------------------------------------
  // Global Toast Notifications
  // -------------------------------------------------------------------------

  showToast(message, type = 'info', duration = 3500) {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast-item toast-${type}`;
    toast.innerHTML = `
      <span class="toast-icon">${type === 'success' ? '✔' : type === 'danger' ? '✖' : type === 'warning' ? '⚠️' : 'ℹ️'}</span>
      <span class="toast-msg">${this.escapeHtml(message)}</span>
    `;

    container.appendChild(toast);

    setTimeout(() => {
      toast.classList.add('fade-out');
      setTimeout(() => toast.remove(), 300);
    }, duration);
  }

  // -------------------------------------------------------------------------
  // Utility
  // -------------------------------------------------------------------------

  escapeHtml(str) {
    if (typeof str !== 'string') return String(str || '');
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }
}

// Initialize application when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  window.app = new WebConsoleApp();
});
