(function () {
  const BacklinkApp = {
    _htmxConfigured: false,
    _delegatesBound: false,
    _logSocket: null,
    _trainerSocket: null,
    _chartRefs: new Map(),

    init() {
      this.installHtmxHelpers();
      this.initCharts();
      this.ensureDelegates();
    },

    installHtmxHelpers() {
      if (this._htmxConfigured || !window.htmx) {
        return;
      }
      this._htmxConfigured = true;

      document.body.addEventListener('htmx:configRequest', (event) => {
        const detail = event.detail;
        const csrf = document.body.dataset.csrf;
        if (csrf) {
          detail.headers['X-CSRF-Token'] = csrf;
        }
        if (detail.headers['Content-Type'] === 'application/json') {
          const payload = this.normalisePayload({ ...detail.parameters });
          detail.parameters = payload;
          if (detail.fetchOptions) {
            detail.fetchOptions.body = JSON.stringify(payload);
          } else {
            detail.fetchOptions = { body: JSON.stringify(payload) };
          }
        }
      });

      document.body.addEventListener('htmx:afterRequest', (event) => {
        const token = event.detail.xhr && event.detail.xhr.getResponseHeader
          ? event.detail.xhr.getResponseHeader('X-CSRF-Token')
          : null;
        if (token) {
          document.body.dataset.csrf = token;
        }
      });

      document.body.addEventListener('htmx:afterSwap', () => {
        this.initCharts();
      });
    },

    ensureDelegates() {
      if (this._delegatesBound) {
        return;
      }
      this._delegatesBound = true;

      document.body.addEventListener('click', (event) => {
        const logButton = event.target.closest('[data-log-url]');
        if (logButton) {
          event.preventDefault();
          this.connectExecutionLog(logButton.dataset.logUrl, logButton.dataset.executionId || '');
          return;
        }
        const trainerButton = event.target.closest('[data-trainer-connect]');
        if (trainerButton) {
          event.preventDefault();
          this.connectTrainerFeed(trainerButton.dataset.trainerConnect);
        }
      });

      window.addEventListener('beforeunload', () => {
        this.closeSockets();
      });
    },

    normalisePayload(params) {
      const result = {};
      Object.entries(params).forEach(([key, value]) => {
        if (key === 'X-CSRF-Token') {
          return;
        }
        const path = this.parseKeyPath(key);
        const converted = this.convertValue(path[path.length - 1], value);
        if (converted === undefined) {
          return;
        }
        this.assignPath(result, path, converted);
      });
      return result;
    },

    parseKeyPath(key) {
      const segments = [];
      let buffer = '';
      for (const char of key) {
        if (char === '[') {
          if (buffer) {
            segments.push(buffer);
            buffer = '';
          }
          continue;
        }
        if (char === ']') {
          if (buffer) {
            segments.push(buffer);
            buffer = '';
          }
          continue;
        }
        buffer += char;
      }
      if (buffer) {
        segments.push(buffer);
      }
      return segments.length ? segments : [key];
    },

    assignPath(target, path, value) {
      let cursor = target;
      for (let index = 0; index < path.length - 1; index += 1) {
        const segment = path[index];
        if (!(segment in cursor) || typeof cursor[segment] !== 'object' || cursor[segment] === null) {
          cursor[segment] = {};
        }
        cursor = cursor[segment];
      }
      cursor[path[path.length - 1]] = value;
    },

    convertValue(field, value) {
      if (value === undefined || value === null) {
        return undefined;
      }
      if (Array.isArray(value)) {
        return value.map((entry) => this.convertValue(field, entry));
      }
      if (typeof value !== 'string') {
        return value;
      }
      const trimmed = value.trim();
      if (field === 'kinds') {
        if (!trimmed) {
          return [];
        }
        return trimmed
          .split(',')
          .map((item) => item.trim())
          .filter((item) => item.length);
      }
      if (trimmed === '') {
        if (field.endsWith('_id') || field.endsWith('_ms') || field.endsWith('_per_minute')) {
          return null;
        }
        return '';
      }
      const lower = trimmed.toLowerCase();
      if (lower === 'true' || lower === 'false') {
        return lower === 'true';
      }
      const numericFields = new Set([
        'category_id',
        'target_id',
        'recipe_id',
        'playwright_timeout_ms',
        'rate_limit_per_minute',
        'min_words',
        'max_words',
        'min_bio_words',
        'max_bio_words',
        'min_caption_words',
        'max_caption_words',
      ]);
      if (numericFields.has(field) || field.endsWith('_id')) {
        const parsed = Number(trimmed);
        if (!Number.isNaN(parsed)) {
          return parsed;
        }
      }
      return trimmed;
    },

    initCharts() {
      this.initCategoryChart();
      this.initHistoryChart();
    },

    initCategoryChart() {
      const canvas = document.getElementById('categoryChart');
      if (!canvas || !window.Chart) {
        return;
      }
      let data = [];
      try {
        data = JSON.parse(canvas.dataset.categories || '[]');
      } catch (error) {
        data = [];
      }
      const labels = data.map((item) => item.category);
      const values = data.map((item) => item.recipe_count);
      this.renderChart(canvas, {
        type: 'doughnut',
        data: {
          labels,
          datasets: [
            {
              data: values,
              backgroundColor: ['#0d6efd', '#198754', '#ffc107', '#dc3545', '#0dcaf0', '#6610f2'],
            },
          ],
        },
        options: {
          plugins: {
            legend: { position: 'bottom' },
          },
        },
      });
    },

    initHistoryChart() {
      const canvas = document.getElementById('historyChart');
      if (!canvas || !window.Chart) {
        return;
      }
      let history = { success: [], failure: [] };
      try {
        history = JSON.parse(canvas.dataset.history || '{}');
      } catch (error) {
        history = { success: [], failure: [] };
      }
      const labels = Array.from(new Set([...history.success.map((item) => item[0]), ...history.failure.map((item) => item[0])])).sort();
      const successValues = labels.map((label) => {
        const record = history.success.find((item) => item[0] === label);
        return record ? record[1] : 0;
      });
      const failureValues = labels.map((label) => {
        const record = history.failure.find((item) => item[0] === label);
        return record ? record[1] : 0;
      });
      this.renderChart(canvas, {
        type: 'line',
        data: {
          labels,
          datasets: [
            {
              label: 'Success',
              data: successValues,
              borderColor: '#198754',
              backgroundColor: 'rgba(25, 135, 84, 0.2)',
            },
            {
              label: 'Failure',
              data: failureValues,
              borderColor: '#dc3545',
              backgroundColor: 'rgba(220, 53, 69, 0.2)',
            },
          ],
        },
        options: {
          scales: {
            y: { beginAtZero: true, precision: 0 },
          },
        },
      });
    },

    renderChart(canvas, config) {
      if (!window.Chart) {
        return;
      }
      if (this._chartRefs.has(canvas)) {
        const chart = this._chartRefs.get(canvas);
        chart.destroy();
      }
      const instance = new window.Chart(canvas.getContext('2d'), config);
      this._chartRefs.set(canvas, instance);
    },

    refreshTargets() {
      this.loadFragment('/partials/targets-table', '#targets-table');
    },

    refreshRecipes() {
      this.loadFragment('/partials/recipes-table', '#recipes-table');
    },

    refreshAssets() {
      this.loadFragment('/partials/ai-assets', '#assets-table');
    },

    refreshCategories() {
      this.loadFragment('/partials/categories-table', '#categories-table');
    },

    refreshCategoryRequests() {
      this.loadFragment('/partials/category-requests', '#category-requests');
    },

    refreshTrainerSessions() {
      this.loadFragment('/partials/trainer-sessions', '#trainer-sessions');
    },

    loadFragment(url, target, swap = 'outerHTML') {
      if (!window.htmx) {
        return;
      }
      window.htmx.ajax('GET', url, { target, swap });
    },

    makeSocketUrl(path) {
      if (!path) {
        return null;
      }
      if (path.startsWith('ws://') || path.startsWith('wss://')) {
        return path;
      }
      const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
      const host = window.location.host;
      return `${protocol}${host}${path}`;
    },

    connectExecutionLog(path, executionId) {
      const url = this.makeSocketUrl(path);
      const card = document.querySelector('[data-log-card]');
      const output = document.querySelector('[data-log-output]');
      const label = document.querySelector('[data-log-label]');
      if (!url || !card || !output || !label) {
        return;
      }
      if (this._logSocket) {
        this._logSocket.close();
      }
      output.textContent = 'Connecting…\n';
      card.classList.remove('d-none');
      label.textContent = `#${executionId}`;
      this._logSocket = new WebSocket(url);
      this._logSocket.addEventListener('message', (event) => {
        output.textContent += event.data;
        output.scrollTop = output.scrollHeight;
      });
      this._logSocket.addEventListener('close', () => {
        this._logSocket = null;
      });
      this._logSocket.addEventListener('error', () => {
        output.textContent += '\n[error] Connection closed.';
      });
    },

    connectTrainerFeed(sessionId) {
      if (!sessionId) {
        return;
      }
      const url = this.makeSocketUrl(`/api/trainer/${sessionId}/events`);
      const card = document.querySelector('[data-trainer-feed-card]');
      const list = document.querySelector('[data-trainer-feed]');
      const label = document.querySelector('[data-trainer-label]');
      if (!url || !card || !list || !label) {
        return;
      }
      if (this._trainerSocket) {
        this._trainerSocket.close();
      }
      list.innerHTML = '';
      card.classList.remove('d-none');
      label.textContent = sessionId;
      this._trainerSocket = new WebSocket(url);
      this._trainerSocket.addEventListener('message', (event) => {
        try {
          const payload = JSON.parse(event.data);
          this.renderTrainerEvent(payload, list);
        } catch (error) {
          const item = document.createElement('li');
          item.className = 'list-group-item';
          item.textContent = event.data;
          list.appendChild(item);
        }
      });
      this._trainerSocket.addEventListener('close', () => {
        this._trainerSocket = null;
      });
    },

    renderTrainerEvent(payload, list) {
      const item = document.createElement('li');
      item.className = 'list-group-item small';
      if (payload.type === 'action') {
        const action = payload.payload || {};
        item.textContent = `${action.action || 'event'} → ${action.selector || ''} ${action.value ? 'value=' + action.value : ''}`;
      } else if (payload.type === 'closed') {
        item.textContent = 'Session closed';
      } else if (payload.type === 'discarded') {
        item.textContent = 'Session discarded';
      } else {
        item.textContent = JSON.stringify(payload);
      }
      list.appendChild(item);
      list.parentElement.scrollTop = list.parentElement.scrollHeight;
    },

    closeSockets() {
      if (this._logSocket) {
        this._logSocket.close();
        this._logSocket = null;
      }
      if (this._trainerSocket) {
        this._trainerSocket.close();
        this._trainerSocket = null;
      }
    },
  };

  window.BacklinkApp = BacklinkApp;
  document.addEventListener('DOMContentLoaded', () => {
    BacklinkApp.init();
  });
})();
