class QrInventorySearchCard extends HTMLElement {
  static get properties() {
    return {
      hass: {},
      _query: { state: true },
      _expanded: { state: true },
    };
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._query = '';
    this._expanded = {};
    this._boundInput = (e) => {
      this._query = (e?.target?.value || '').toString();
      this._render();
    };
  }

  setConfig(config) {
    if (!config || typeof config !== 'object') {
      throw new Error('Invalid configuration');
    }
    this._config = {
      title: config.title || 'QR Inventory',
      entity: config.entity || 'sensor.qr_inventory_detected_list',
      search_placeholder: config.search_placeholder || 'Search project, text, zone, location…',
      show_members: config.show_members !== false,
      show_locations: config.show_locations !== false,
      show_variants: config.show_variants !== false,
      show_count_badge: config.show_count_badge !== false,
      compact: !!config.compact,
      print_base_url: config.print_base_url || '',
      print_path_template: config.print_path_template || '/print/project/{group_key}',
      empty_text: config.empty_text || 'No active detections',
      no_match_text: config.no_match_text || 'No matches',
      card_height: config.card_height || '',
    };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  get hass() {
    return this._hass;
  }

  getCardSize() {
    const items = this._getItems();
    return Math.max(3, Math.min(12, 2 + (items?.length || 0)));
  }

  static getStubConfig() {
    return {
      type: 'custom:qr-inventory-search-card',
      entity: 'sensor.qr_inventory_detected_list',
      title: 'QR Inventory',
    };
  }

  _escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  _normalizeStringArray(value) {
    if (!value) return [];
    if (Array.isArray(value)) return value.map((v) => String(v)).filter(Boolean);
    return [String(value)].filter(Boolean);
  }

  _getStateObj() {
    if (!this._hass || !this._config?.entity) return null;
    return this._hass.states[this._config.entity] || null;
  }

  _getItems() {
    const stateObj = this._getStateObj();
    const attrs = stateObj?.attributes || {};
    const rawItems = attrs.items;

    if (Array.isArray(rawItems)) {
      return rawItems.map((item, idx) => {
        const groupKey = item?.group_key ?? item?.key ?? item?.id ?? `item_${idx}`;
        const label = item?.label ?? item?.display_label ?? item?.name ?? String(groupKey);
        const members = this._normalizeStringArray(item?.members ?? item?.zones ?? item?.children);
        const locations = this._normalizeStringArray(item?.locations);
        const payloadVariants = this._normalizeStringArray(item?.payload_variants ?? item?.variants ?? item?.payloads);
        const searchText = [
          groupKey,
          label,
          ...members,
          ...locations,
          ...payloadVariants,
        ].join(' ').toLowerCase();
        return {
          group_key: String(groupKey),
          label: String(label),
          members,
          locations,
          payload_variants: payloadVariants,
          search_text: searchText,
        };
      });
    }

    const rawLines = attrs.lines;
    if (Array.isArray(rawLines)) {
      return rawLines.map((line, idx) => {
        const text = String(line);
        const groupKey = text.split(':')[0]?.trim() || `item_${idx}`;
        return {
          group_key: groupKey,
          label: text,
          members: [],
          locations: [],
          payload_variants: [],
          search_text: text.toLowerCase(),
        };
      });
    }

    const rawText = attrs.text || stateObj?.state;
    if (rawText && rawText !== 'unknown' && rawText !== 'unavailable') {
      return String(rawText)
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line, idx) => ({
          group_key: line.split(':')[0]?.trim() || `item_${idx}`,
          label: line,
          members: [],
          locations: [],
          payload_variants: [],
          search_text: line.toLowerCase(),
        }));
    }

    return [];
  }

  _filteredItems() {
    const items = this._getItems();
    const q = (this._query || '').trim().toLowerCase();
    if (!q) return items;
    return items.filter((item) => item.search_text.includes(q));
  }

  _toggleExpand(key) {
    this._expanded = {
      ...this._expanded,
      [key]: !this._expanded[key],
    };
    this._render();
  }

  _buildPrintUrl(groupKey) {
    if (!this._config?.print_base_url) return '';
    const base = String(this._config.print_base_url).replace(/\/$/, '');
    const path = String(this._config.print_path_template || '/print/project/{group_key}')
      .replace('{group_key}', encodeURIComponent(groupKey));
    return `${base}${path}`;
  }

  _rowHtml(item) {
    const expanded = !!this._expanded[item.group_key];
    const hasDetails = item.members.length || item.locations.length || item.payload_variants.length;
    const printUrl = this._buildPrintUrl(item.group_key);
    const memberText = item.members.join(', ');
    const locationText = item.locations.join(', ');
    const variantText = item.payload_variants.join(', ');

    return `
      <div class="row">
        <div class="row-main">
          <button class="row-title" data-key="${this._escapeHtml(item.group_key)}" title="Toggle details">
            <span class="row-key">${this._escapeHtml(item.group_key)}</span>
            <span class="row-sep">·</span>
            <span class="row-label">${this._escapeHtml(item.label)}</span>
          </button>
          <div class="row-actions">
            ${this._config.show_count_badge ? `<span class="badge">${item.members.length || item.locations.length || 0}</span>` : ''}
            ${hasDetails ? `<button class="ghost" data-key="${this._escapeHtml(item.group_key)}">${expanded ? 'Hide' : 'Show'}</button>` : ''}
            ${printUrl ? `<a class="ghost link" href="${this._escapeHtml(printUrl)}" target="_blank" rel="noreferrer">Print</a>` : ''}
          </div>
        </div>
        ${expanded && hasDetails ? `
          <div class="details">
            ${this._config.show_members && item.members.length ? `
              <div class="detail-block">
                <div class="detail-label">Members</div>
                <div class="detail-value">${this._escapeHtml(memberText)}</div>
              </div>
            ` : ''}
            ${this._config.show_locations && item.locations.length ? `
              <div class="detail-block">
                <div class="detail-label">Locations</div>
                <div class="detail-value">${this._escapeHtml(locationText)}</div>
              </div>
            ` : ''}
            ${this._config.show_variants && item.payload_variants.length ? `
              <div class="detail-block">
                <div class="detail-label">Payload variants</div>
                <div class="detail-value">${this._escapeHtml(variantText)}</div>
              </div>
            ` : ''}
          </div>
        ` : ''}
      </div>
    `;
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const stateObj = this._getStateObj();
    const items = this._getItems();
    const filtered = this._filteredItems();
    const query = this._escapeHtml(this._query || '');
    const hasState = !!stateObj;
    const stateText = hasState ? String(stateObj.state ?? '') : 'Entity not found';
    const bodyHtml = !hasState
      ? `<div class="empty">Entity not found: ${this._escapeHtml(this._config.entity)}</div>`
      : items.length === 0
        ? `<div class="empty">${this._escapeHtml(this._config.empty_text)}</div>`
        : filtered.length === 0
          ? `<div class="empty">${this._escapeHtml(this._config.no_match_text)}</div>`
          : filtered.map((item) => this._rowHtml(item)).join('');

    const heightStyle = this._config.card_height ? `max-height:${this._escapeHtml(this._config.card_height)};` : '';

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
        }
        ha-card {
          overflow: hidden;
        }
        .wrap {
          padding: 16px;
        }
        .header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          margin-bottom: 12px;
        }
        .title {
          font-size: 1.2rem;
          font-weight: 600;
          line-height: 1.3;
        }
        .meta {
          color: var(--secondary-text-color);
          font-size: 0.85rem;
          white-space: nowrap;
        }
        .search {
          margin-bottom: 12px;
        }
        input[type="search"] {
          width: 100%;
          box-sizing: border-box;
          padding: 10px 12px;
          border-radius: 10px;
          border: 1px solid var(--divider-color);
          background: var(--card-background-color, var(--primary-background-color));
          color: var(--primary-text-color);
          outline: none;
          font: inherit;
        }
        input[type="search"]:focus {
          border-color: var(--primary-color);
          box-shadow: 0 0 0 1px var(--primary-color);
        }
        .list {
          display: grid;
          gap: 10px;
          overflow: auto;
          ${heightStyle}
        }
        .row {
          border: 1px solid var(--divider-color);
          border-radius: 12px;
          padding: 10px 12px;
          background: color-mix(in srgb, var(--card-background-color) 92%, var(--primary-background-color));
        }
        .row-main {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 10px;
        }
        .row-title {
          appearance: none;
          background: transparent;
          border: 0;
          padding: 0;
          margin: 0;
          cursor: pointer;
          text-align: left;
          color: inherit;
          flex: 1;
          min-width: 0;
          font: inherit;
        }
        .row-key {
          font-weight: 700;
        }
        .row-sep {
          color: var(--secondary-text-color);
          margin: 0 6px;
        }
        .row-label {
          color: var(--primary-text-color);
          word-break: break-word;
        }
        .row-actions {
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
        }
        .badge {
          font-size: 0.8rem;
          line-height: 1;
          padding: 6px 8px;
          border-radius: 999px;
          background: var(--secondary-background-color);
          color: var(--secondary-text-color);
        }
        .ghost, .link {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          border: 1px solid var(--divider-color);
          background: transparent;
          color: var(--primary-text-color);
          border-radius: 999px;
          padding: 6px 10px;
          font: inherit;
          font-size: 0.8rem;
          cursor: pointer;
          text-decoration: none;
        }
        .details {
          margin-top: 10px;
          display: grid;
          gap: 8px;
        }
        .detail-block {
          display: grid;
          gap: 2px;
        }
        .detail-label {
          color: var(--secondary-text-color);
          font-size: 0.75rem;
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }
        .detail-value {
          color: var(--primary-text-color);
          word-break: break-word;
        }
        .empty {
          padding: 8px 0;
          color: var(--secondary-text-color);
        }
      </style>
      <ha-card>
        <div class="wrap">
          <div class="header">
            <div class="title">${this._escapeHtml(this._config.title)}</div>
            <div class="meta">${this._escapeHtml(stateText)} groups</div>
          </div>
          <div class="search">
            <input type="search" placeholder="${this._escapeHtml(this._config.search_placeholder)}" value="${query}" />
          </div>
          <div class="list">
            ${bodyHtml}
          </div>
        </div>
      </ha-card>
    `;

    const input = this.shadowRoot.querySelector('input[type="search"]');
    if (input) {
      input.removeEventListener('input', this._boundInput);
      input.addEventListener('input', this._boundInput);
    }

    this.shadowRoot.querySelectorAll('button[data-key]').forEach((el) => {
      el.addEventListener('click', (e) => {
        const key = e.currentTarget?.getAttribute('data-key');
        if (key) this._toggleExpand(key);
      });
    });
  }
}

customElements.define('qr-inventory-search-card', QrInventorySearchCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'qr-inventory-search-card',
  name: 'QR Inventory Search Card',
  description: 'Search and browse grouped QR inventory detections from a sensor attribute.',
});
