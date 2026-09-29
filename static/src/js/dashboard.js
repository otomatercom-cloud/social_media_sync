/** @odoo-module **/

import { Component, useState, useRef, onWillStart, onMounted, onPatched, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const QUALITY_COLORS = {
    'New':'#6366f1','First Attempt':'#3b82f6','Hot':'#ef4444','Warm':'#f59e0b',
    'Cold':'#64748b','Not Responding':'#94a3b8','Follow Up':'#8b5cf6',
    'Admission':'#10b981','Language Barrier':'#f97316','Crash Lead':'#dc2626',
};

class OtmSmDashboard extends Component {
    static template = "social_media_sync.Dashboard";
    static props = ["*"];

    setup() {
        this.orm          = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            loading: true, syncing: false,
            period: 'month', campaignFilter: '', adSearch: '',
            refreshInterval: 0, data: {},
        });
        this._refreshTimer = null;
        this.rootRef = useRef("uc_root");
        this._onResize = () => this._fixHeight();
        onWillStart(() => this._loadData());
        onMounted(() => {
            this._renderCharts();
            this._startAutoRefresh();
            this._fixHeight();
            window.addEventListener("resize", this._onResize);
        });
        onPatched(() => { if (!this.state.loading) { this._renderCharts(); this._fixHeight(); } });
        onWillUnmount(() => {
            this._stopAutoRefresh();
            window.removeEventListener("resize", this._onResize);
        });
    }

    // ── Force a real, guaranteed-working scroll area ────────────────────────
    // Odoo's backend layout (and/or theme overrides) can clip this component
    // in ways that vary by view/screen, so instead of guessing at ancestor
    // CSS, we measure the actual remaining viewport space at runtime and pin
    // our own element to exactly that height with its own scrollbar.
    _fixHeight() {
        const el = this.rootRef.el;
        if (!el) return;
        const top = el.getBoundingClientRect().top;
        const available = Math.max(window.innerHeight - top, 300);
        el.style.height = available + "px";
        el.style.maxHeight = available + "px";
        el.style.overflowY = "auto";
        el.style.overflowX = "hidden";
        el.style.boxSizing = "border-box";
    }

    // ── Data ──────────────────────────────────────────────────────────────
    async _loadData() {
        try {
            this.state.data = await this.orm.call("otm.sm.sheet.config","get_dashboard_data",[],{});
        } catch(e) {
            console.error(e);
            this.notification.add("Failed to load dashboard data.", { type: "danger" });
        } finally { this.state.loading = false; }
    }

    // ── Auto refresh ──────────────────────────────────────────────────────
    async _startAutoRefresh() {
        this._stopAutoRefresh();
        try {
            const secs = await this.orm.call("otm.sm.sheet.config","get_sync_interval",[],{});
            const ms = Math.max((secs||300),60)*1000;
            this.state.refreshInterval = Math.round(ms/60000);
            this._refreshTimer = setInterval(() => this._loadData(), ms);
        } catch(e) {
            this._refreshTimer = setInterval(() => this._loadData(), 5*60*1000);
        }
    }
    _stopAutoRefresh() { if (this._refreshTimer) { clearInterval(this._refreshTimer); this._refreshTimer = null; } }

    // ── Period ────────────────────────────────────────────────────────────
    setPeriod(p) { this.state.period = p; }
    periodLabel() { return {today:'Today',week:'This Week',month:'This Month',all:'All Time'}[this.state.period]; }

    // ── KPI helpers ───────────────────────────────────────────────────────
    getPeriodLeads() {
        const kpi = this.state.data.kpi || {};
        return {today: kpi.today, week: kpi.week, month: kpi.month, all: kpi.total}[this.state.period] || 0;
    }
    getPeriodAdmissions() {
        return (this.state.data.adset_perf||[]).reduce((s,r)=>s+(r[this.state.period]?.admissions||0),0);
    }

    // ── Best adset ────────────────────────────────────────────────────────
    getBestAdsetData(metric) {
        const p=this.state.period, rows=this.state.data.adset_perf||[];
        const f=rows.filter(r=>r[p]&&r[p][metric]>0);
        return f.length ? f.reduce((b,r)=>r[p][metric]>b[p][metric]?r:b) : null;
    }
    getBestAdset(metric) {
        const b=this.getBestAdsetData(metric); if(!b) return '—';
        return `${b.adset} (${b[this.state.period][metric]})`;
    }

    // ── Conversion rate ───────────────────────────────────────────────────
    getConvRate(d) { return (d&&d.leads) ? Math.round((d.admissions/d.leads)*100) : 0; }

    // ── Campaign ──────────────────────────────────────────────────────────
    getCampaignPerf() {
        return (this.state.data.campaign_perf||[]).filter(c=>c[this.state.period]&&c[this.state.period].leads>0);
    }
    getCampaignList() { return [...new Set((this.state.data.adset_perf||[]).map(r=>r.campaign))]; }
    getCampShare(val,isAdm=false) {
        const rows=this.getCampaignPerf();
        const max=Math.max(...rows.map(r=>isAdm?r[this.state.period].admissions:r[this.state.period].leads),1);
        return Math.round((val/max)*100);
    }

    // ── Adset ─────────────────────────────────────────────────────────────
    onCampaignFilter(ev) { this.state.campaignFilter=ev.target.value; }
    getFilteredAdsets() {
        const p=this.state.period;
        return (this.state.data.adset_perf||[])
            .filter(r=>r[p]&&r[p].leads>0)
            .filter(r=>!this.state.campaignFilter||r.campaign===this.state.campaignFilter)
            .sort((a,b)=>b[p].leads-a[p].leads);
    }
    getAdsetShare(val,isAdm=false) {
        const rows=this.getFilteredAdsets(),p=this.state.period;
        const max=Math.max(...rows.map(r=>isAdm?r[p].admissions:r[p].leads),1);
        return Math.round((val/max)*100);
    }

    // ── Per Ad ────────────────────────────────────────────────────────────
    onAdSearch(ev) { this.state.adSearch = ev.target.value.toLowerCase(); }
    getFilteredAds() {
        const q=this.state.adSearch, p=this.state.period;
        return (this.state.data.ad_perf||[])
            .filter(a=>!q||(a.ad_id||'').toLowerCase().includes(q)||(a.source_camp||'').toLowerCase().includes(q)||(a.campaign||'').toLowerCase().includes(q))
            .sort((a,b)=>(b[p]||0)-(a[p]||0));
    }

    // ── Sync ──────────────────────────────────────────────────────────────
    async syncNow() {
        this.state.syncing = true;
        try {
            const cfgs = await this.orm.searchRead("otm.sm.sheet.config",[["active","=",true]],["id","name"]);
            if (!cfgs.length) { this.notification.add("No active sheet configs.", {type:"warning"}); return; }
            for (const c of cfgs) await this.orm.call("otm.sm.sheet.config","action_sync_now",[c.id],{});
            this.notification.add("Sync complete! Refreshing…",{type:"success"});
            this.state.loading=true; await this._loadData();
        } catch(e) { this.notification.add("Sync failed: "+(e.message||e),{type:"danger"}); }
        finally { this.state.syncing=false; }
    }

    // ── Charts ────────────────────────────────────────────────────────────
    _renderCharts() {
        if (this.state.loading) return;
        if (!window.Chart) {
            const s=document.createElement("script");
            s.src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js";
            s.onload=()=>this._buildAllCharts(); document.head.appendChild(s);
        } else { this._buildAllCharts(); }
    }
    _destroy(id) { const el=document.getElementById(id); if(el&&el._chartInstance){el._chartInstance.destroy();el._chartInstance=null;} }
    _buildAllCharts() { this._buildDailyChart(); this._buildQualityChart(); }

    _buildDailyChart() {
        this._destroy("uc_daily_chart");
        const el=document.getElementById("uc_daily_chart"); if(!el) return;
        const daily=this.state.data.daily_data||[];
        el._chartInstance=new window.Chart(el,{
            type:"line",
            data:{
                labels:daily.map(d=>{const dt=new Date(d.date);return dt.toLocaleDateString("en-IN",{day:"numeric",month:"short"});}),
                datasets:[
                    {label:"Prospects",data:daily.map(d=>d.leads),fill:true,backgroundColor:"rgba(99,102,241,0.12)",borderColor:"#6366f1",tension:0.4,pointRadius:4,pointBackgroundColor:"#6366f1"},
                    {label:"Admissions",data:daily.map(d=>d.admissions),fill:false,borderColor:"#10b981",backgroundColor:"#10b981",tension:0.4,pointRadius:4,borderDash:[4,3]},
                ],
            },
            options:{responsive:true,plugins:{legend:{position:"top"},tooltip:{mode:"index"}},scales:{y:{beginAtZero:true,ticks:{stepSize:1}},x:{grid:{display:false}}}},
        });
    }

    _buildQualityChart() {
        this._destroy("uc_quality_chart");
        const el=document.getElementById("uc_quality_chart"); if(!el) return;
        const entries=Object.entries(this.state.data.quality_data||{}).filter(([,v])=>v>0);
        if(!entries.length) return;
        el._chartInstance=new window.Chart(el,{
            type:"doughnut",
            data:{
                labels:entries.map(([k])=>k),
                datasets:[{data:entries.map(([,v])=>v),backgroundColor:entries.map(([k])=>QUALITY_COLORS[k]||"#94a3b8"),borderWidth:2,borderColor:"#fff"}],
            },
            options:{responsive:true,cutout:"60%",plugins:{legend:{position:"right",labels:{boxWidth:12,font:{size:11}}}}},
        });
    }
}

registry.category("actions").add("otm_sm_dashboard", OtmSmDashboard);
