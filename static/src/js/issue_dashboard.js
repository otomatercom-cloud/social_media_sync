/** @odoo-module **/

import { Component, useState, useRef, onWillStart, onMounted, onPatched } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

class OtmSmIssueDashboard extends Component {
    static template = "social_media_sync.IssueDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.rootRef = useRef("iss_root");
        this.state = useState({
            loading: true,
            configId: "", fieldName: "", status: "open", search: "",
            data: { kpi: {}, by_field: [], by_reason: [], configs: [], rows: [] },
        });
        this._timer = null;
        onWillStart(() => this._load());
        onMounted(() => this._fixHeight());
        onPatched(() => this._fixHeight());
    }

    _fixHeight() {
        const el = this.rootRef.el;
        if (!el) return;
        const available = Math.max(window.innerHeight - el.getBoundingClientRect().top, 300);
        el.style.height = available + "px";
        el.style.overflowY = "auto";
        el.style.boxSizing = "border-box";
    }

    async _load() {
        try {
            this.state.data = await this.orm.call("otm.sm.lead.issue", "get_issue_dashboard_data", [], {
                config_id: this.state.configId ? parseInt(this.state.configId) : false,
                field_name: this.state.fieldName || false,
                state: this.state.status,
                search: this.state.search || false,
            });
        } catch (e) {
            console.error(e);
            this.notification.add("Failed to load lead issues.", { type: "danger" });
        } finally {
            this.state.loading = false;
        }
    }

    onConfig(ev) { this.state.configId = ev.target.value; this._load(); }
    onStatus(status) { this.state.status = status; this._load(); }
    onField(key) { this.state.fieldName = this.state.fieldName === key ? "" : key; this._load(); }
    onSearch(ev) {
        this.state.search = ev.target.value;
        clearTimeout(this._timer);
        this._timer = setTimeout(() => this._load(), 400);
    }
    refresh() { this._load(); }

    openLead(row) {
        this.action.doAction({
            type: "ir.actions.act_window", res_model: "leads.logic", res_id: row.lead_id,
            views: [[false, "form"]], target: "current",
        });
    }
    async resolve(row) {
        await this.orm.call("otm.sm.lead.issue", "action_resolve", [[row.id]]);
        this._load();
    }
    async reopen(row) {
        await this.orm.call("otm.sm.lead.issue", "action_reopen", [[row.id]]);
        this._load();
    }
    barWidth(count) {
        const max = Math.max(...this.state.data.by_field.map((f) => f.count), 1);
        return Math.round((count / max) * 100);
    }
}

registry.category("actions").add("otm_sm_issue_dashboard", OtmSmIssueDashboard);
