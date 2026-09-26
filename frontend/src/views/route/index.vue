<template>
  <section class="page" data-module="route">
    <header class="page-head">
      <div>
        <h2>运输路线管理</h2>
        <p class="page-desc">
          里程、耗时、过路费按口径自动取数：依出发地、目的地与有序途经节点逐段累加；同一线路编号不同途经节点分别成案；
          里程超合理上限会被标记并说明原因。复核人看到的里程依据始终与录入时一致。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记路线方案</button>
        <button class="btn" type="button" :disabled="recalculating" @click="recalculateAll">
          {{ recalculating ? '重算中…' : '按当前口径全部重算' }}
        </button>
        <button class="btn" type="button" @click="exportRows">导出清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div class="rule-bar">
      <span class="rule-tag">当前生效口径：<b>{{ ruleVersion }}</b></span>
      <span class="rule-hint">切换口径版本后点「全部重算」，既有记录会按新口径试算，录入依据保留、待复核重新定基。</span>
      <label class="rule-switch">
        发布版本
        <select :value="ruleVersion" @change="activateVersion(($event.target as HTMLSelectElement).value)">
          <option v-for="v in versions" :key="v.version" :value="v.version">{{ v.version }}</option>
        </select>
      </label>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>路线编号 / 指纹 / 起讫地</span>
        <input v-model="keyword" placeholder="如 ROUT-0001 或 武汉" />
      </label>
      <label class="filter-item">
        <span>路线状态</span>
        <select v-model="statusFilter">
          <option value="">全部</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table route-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-deviated': isDeviated(row), 'row-noref': !hasReference(row), 'row-stale': row.stale }">
          <td>{{ row['路线编号'] }}</td>
          <td>{{ row['出发地'] }}</td>
          <td>{{ row['目的地'] }}</td>
          <td class="cell-waypoints"><span v-if="row['途经节点']">{{ row['途经节点'] }}</span><span v-else class="muted">缺失</span></td>
          <td>{{ row['车型类别'] || '—' }}</td>
          <td>{{ row['预计里程'] ?? '—' }}</td>
          <td>{{ row['预计耗时'] ?? '—' }}</td>
          <td>{{ row['过路费用'] != null ? `¥${row['过路费用']}` : '—' }}</td>
          <td><span class="ver-badge">{{ row['口径版本'] }}</span></td>
          <td>
            <span v-if="isDeviated(row)" class="badge badge-danger">偏离</span>
            <span v-else-if="hasReference(row)" class="badge badge-ok">正常</span>
            <span v-else class="badge badge-muted">无参考值</span>
            <span v-if="row.stale" class="badge badge-warn">待重核定基</span>
          </td>
          <td class="cell-note">{{ row['参考值说明'] || '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">里程依据</button>
            <button class="link" type="button" @click="recalculateOne(row)">重算</button>
            <button class="link" type="button" :disabled="!row.stale" @click="rebaseline(row)">重新定基</button>
            <template v-for="action in actions" :key="action">
              <button class="link" type="button" @click="runAction(action, row)">{{ action }}</button>
            </template>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无运输路线数据，可先登记路线方案</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条运输路线记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="okMessage" class="ok-text">{{ okMessage }}</span>
    </footer>

    <!-- 登记路线方案 -->
    <div v-if="creating" class="modal-mask" @click.self="creating = false">
      <div class="modal">
        <h3>登记路线方案</h3>
        <p class="modal-sub">填写出发地、目的地、有序途经节点与车型类别，里程/耗时/过路费将按口径自动计算并冻结。</p>
        <div class="form-grid">
          <label>
            <span>路线编号 *</span>
            <input v-model="form['路线编号']" placeholder="如 ROUT-0010" />
          </label>
          <label>
            <span>车型类别 *</span>
            <select v-model="form['车型类别']">
              <option value="">请选择</option>
              <option v-for="cat in categories" :key="cat" :value="cat">{{ cat }}</option>
            </select>
          </label>
          <label>
            <span>出发地 *</span>
            <input v-model="form['出发地']" placeholder="如 上海" list="node-list" />
          </label>
          <label>
            <span>目的地 *</span>
            <input v-model="form['目的地']" placeholder="如 武汉" list="node-list" />
          </label>
          <label class="span-2">
            <span>途经节点（按顺序，用「、」或「→」分隔）*</span>
            <input v-model="form['途经节点']" placeholder="如 苏州、南京、合肥" list="node-list" />
            <small class="field-hint">路网节点：{{ knownNodes.slice(0, 12).join('、') }}{{ knownNodes.length > 12 ? ' 等' : '' }}</small>
          </label>
        </div>
        <datalist id="node-list">
          <option v-for="n in knownNodes" :key="n" :value="n"></option>
        </datalist>

        <div v-if="preview" class="preview-box" :class="{ 'preview-warn': !preview.ok || preview.deviated }">
          <template v-if="preview.ok">
            <div>预计里程：<b>{{ preview.distance_km }} km</b></div>
            <div>预计耗时：<b>{{ preview.duration_text }}</b>（{{ preview.duration_hours }} 小时）</div>
            <div>过路费用参考：<b>¥{{ preview.toll_fee }}</b></div>
            <div>口径版本：{{ preview.basis?.['口径版本'] }}　路线指纹：{{ (preview.basis?.['节点链'] ?? []).join(' &gt; ') }}</div>
            <div v-if="preview.deviated" class="error-text">⚠ {{ preview.deviation_reason }}</div>
            <div v-else-if="preview.reason" class="warn-text">⚠ {{ preview.reason }}</div>
          </template>
          <template v-else>
            <div class="error-text">⚠ {{ preview.reason }}</div>
            <div class="field-hint">此情况下不会生成参考值，仍可保存记录，待补全节点后再重算。</div>
          </template>
        </div>

        <div v-if="createError" class="error-text">{{ createError }}</div>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="creating = false">取消</button>
          <button class="btn" type="button" @click="previewForm">试算口径</button>
          <button class="btn primary" type="button" :disabled="saving" @click="submitCreate">{{ saving ? '保存中…' : '保存并冻结依据' }}</button>
        </div>
      </div>
    </div>

    <!-- 里程依据明细 -->
    <div v-if="detail" class="modal-mask" @click.self="detail = null">
      <div class="modal modal-wide">
        <h3>里程依据 · {{ detail['路线编号'] }} <span class="ver-badge">{{ detail['口径版本'] }}</span></h3>
        <p class="modal-sub">
          路线指纹：{{ detail['路线指纹'] }}。以下为<strong>录入时冻结</strong>的计算依据，规则改动也不会改写它；
          复核人看到的口径与录入人完全一致。
        </p>

        <div v-if="detail.frozen_ok" class="basis-grid">
          <div><span>预计里程</span><b>{{ detail.frozen_distance_km }} km</b></div>
          <div><span>预计耗时</span><b>{{ detail.frozen_duration_text }}</b></div>
          <div><span>过路费用</span><b>¥{{ detail.frozen_toll_fee }}</b></div>
          <div><span>平均时速</span><b>{{ detail.frozen_basis['平均时速kmh'] }} km/h</b></div>
          <div><span>休整预留</span><b>{{ detail.frozen_basis['休整预留h'] }} 小时</b></div>
          <div><span>直达里程</span><b>{{ detail.frozen_basis['直达里程km'] ?? '—' }} km</b></div>
          <div><span>偏离上限</span><b>{{ detail.frozen_basis['偏离上限km'] ?? '—' }} km</b></div>
          <div><span>是否偏离</span><b :class="detail.frozen_deviated ? 'error-text' : 'ok-text'">{{ detail.frozen_deviated ? '偏离' : '正常' }}</b></div>
        </div>
        <div v-else class="basis-empty">未生成参考值：{{ detail.frozen_reason }}</div>

        <table v-if="detail.frozen_basis" class="data-table segment-table">
          <thead>
            <tr><th>#</th><th>起点</th><th>终点</th><th>区间里程 (km)</th></tr>
          </thead>
          <tbody>
            <tr v-for="(seg, i) in detail.frozen_basis['分段里程']" :key="i">
              <td>{{ i + 1 }}</td><td>{{ seg.from }}</td><td>{{ seg.to }}</td><td>{{ seg.km }}</td>
            </tr>
          </tbody>
        </table>

        <div v-if="detail.frozen_deviated" class="deviation-box">偏离原因：{{ detail.frozen_deviation_reason }}</div>

        <div v-if="detail.recalc_ok !== null" class="recalc-box">
          <h4>按当前口径 ({{ ruleVersion }}) 的待确认试算</h4>
          <template v-if="detail.recalc_ok">
            <div>里程 {{ detail.recalc_distance_km }} km ｜ 耗时 {{ detail.recalc_duration_text }} ｜ 过路费 ¥{{ detail.recalc_toll_fee }}
              <span v-if="detail.recalc_deviated" class="badge badge-danger">新判偏离</span>
            </div>
            <div v-if="detail.recalc_deviation_reason" class="warn-text">{{ detail.recalc_deviation_reason }}</div>
          </template>
          <div v-else class="error-text">新口径未能生成参考值：{{ detail.recalc_reason }}</div>
          <div class="field-hint">试算结果不会覆盖上方录入依据，确认无误后点「重新定基」。</div>
        </div>

        <div v-if="detail['口径变更历史'] && detail['口径变更历史'].length" class="history-box">
          <h4>口径变更历史</h4>
          <ul>
            <li v-for="(h, i) in detail['口径变更历史']" :key="i">
              {{ h['替换时间'] }} 由 {{ h['原口径版本'] }}（{{ h['原里程km'] }}km / {{ h['原耗时'] }} / ¥{{ h['原过路费'] }}）替换为新版本，旧依据已留痕。
            </li>
          </ul>
        </div>

        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="detail = null">关闭</button>
          <button class="btn" type="button" @click="recalculateOne(detail)">按当前口径重算</button>
          <button class="btn primary" type="button" :disabled="!detail.stale" @click="rebaseline(detail)">确认并重新定基</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>
interface Preview {
  ok: boolean
  reason: string
  deviated?: boolean
  deviation_reason?: string
  distance_km?: number | null
  duration_hours?: number | null
  duration_text?: string
  toll_fee?: number | null
  basis?: Record<string, any>
}

const ENDPOINT = '/api/route'
const columns = ['路线编号', '出发地', '目的地', '途经节点', '车型类别', '预计里程', '预计耗时', '过路费用', '口径版本', '里程判定', '参考值说明']
const actions = ['启用路线', '停用路线', '废弃路线']
const statuses = ['可用', '不可用', '备选', '已废弃']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const okMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const recalculating = ref(false)

const versions = ref<{ version: string; note: string; active: boolean }[]>([])
const ruleVersion = ref('')
const categories = ref<string[]>([])
const knownNodes = ref<string[]>([])

const creating = ref(false)
const saving = ref(false)
const createError = ref('')
const preview = ref<Preview | null>(null)
const form = ref<Row>({ '路线编号': '', '出发地': '', '目的地': '', '途经节点': '', '车型类别': '' })

const detail = ref<Row | null>(null)

const stats = computed(() => [
  { label: '路线方案总数', value: rows.value.length },
  { label: '同编号多节点方案', value: countDuplicateCodes() },
  { label: '里程偏离', value: rows.value.filter(isDeviated).length },
  { label: '待重核定基', value: rows.value.filter((r) => r.stale).length },
  { label: '无参考值', value: rows.value.filter((r) => !hasReference(r)).length },
])

function countDuplicateCodes(): number {
  const seen = new Map<string, number>()
  rows.value.forEach((r) => seen.set(r['路线编号'], (seen.get(r['路线编号']) ?? 0) + 1))
  let extra = 0
  seen.forEach((n) => { if (n > 1) extra += n })
  return extra
}

function hasReference(row: Row): boolean {
  return row.frozen_ok === true
}
function isDeviated(row: Row): boolean {
  return row.frozen_deviated === true
}

function flashOk(msg: string) {
  okMessage.value = msg
  errorMessage.value = ''
  setTimeout(() => { okMessage.value = '' }, 6000)
}
function flashError(msg: string) {
  errorMessage.value = msg
}

async function loadMeta() {
  try {
    const rv = await (await request(`${ENDPOINT}/rules/versions`)).json()
    ruleVersion.value = rv.current
    versions.value = rv.versions
    categories.value = rv.vehicle_categories
    const nodes = await (await request(`${ENDPOINT}/rules/nodes`)).json()
    knownNodes.value = nodes.nodes
  } catch (e) {
    flashError(e instanceof Error ? e.message : '口径配置读取失败')
  }
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  form.value = { '路线编号': '', '出发地': '', '目的地': '', '途经节点': '', '车型类别': '' }
  preview.value = null
  createError.value = ''
  creating.value = true
}

async function previewForm() {
  createError.value = ''
  const missing = ['路线编号', '出发地', '目的地'].filter((f) => !String(form.value[f] ?? '').trim())
  if (missing.length) {
    createError.value = `缺少必填字段：${missing.join('、')}`
    preview.value = null
    return
  }
  await computePreview()
}

async function computePreview() {
  // 调后端试算接口：与登记共用同一套口径，但不落库。
  try {
    const resp = await request(`${ENDPOINT}/preview`, {
      method: 'POST',
      body: JSON.stringify({ values: form.value }),
    })
    const data = await resp.json()
    if (!data.ok) {
      preview.value = { ok: false, reason: data.message }
      return
    }
    const e: Row = data.entry
    preview.value = {
      ok: e.frozen_ok === true,
      reason: e.frozen_ok ? (e.frozen_reason || '') : e.frozen_reason,
      deviated: e.frozen_deviated === true,
      deviation_reason: e.frozen_deviation_reason,
      distance_km: e.frozen_distance_km,
      duration_hours: e.frozen_duration_hours,
      duration_text: e.frozen_duration_text,
      toll_fee: e.frozen_toll_fee,
      basis: e.frozen_basis,
    }
  } catch (e) {
    preview.value = { ok: false, reason: '试算失败' }
  }
}

async function submitCreate() {
  createError.value = ''
  const missing = ['路线编号', '出发地', '目的地'].filter((f) => !String(form.value[f] ?? '').trim())
  if (missing.length) {
    createError.value = `缺少必填字段：${missing.join('、')}`
    return
  }
  saving.value = true
  try {
    const resp = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values: form.value }) })
    const data = await resp.json()
    if (!data.ok) {
      createError.value = data.message
      return
    }
    creating.value = false
    flashOk(data.message)
    await reload()
  } catch (e) {
    createError.value = e instanceof Error ? e.message : '登记失败'
  } finally {
    saving.value = false
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  query.set('size', '200')
  try {
    const data = await (await request(`${ENDPOINT}?${query.toString()}`)).json()
    rows.value = data.items ?? []
    total.value = data.total ?? rows.value.length
  } catch (e) {
    flashError(e instanceof Error ? e.message : '运输路线列表读取失败')
  }
}

async function refreshDetailFrom(row: Row) {
  const fresh = await (await request(`${ENDPOINT}/${row.id}`)).json()
  detail.value = fresh
  await reload()
  return fresh
}

async function openDetail(row: Row) {
  detail.value = await (await request(`${ENDPOINT}/${row.id}`)).json()
}

async function recalculateOne(row: Row) {
  try {
    const resp = await request(`${ENDPOINT}/${row.id}/recalculate`, { method: 'POST' })
    const data = await resp.json()
    if (!data.ok) { flashError(data.message); return }
    flashOk(data.message)
    await refreshDetailFrom(row)
  } catch (e) {
    flashError(e instanceof Error ? e.message : '重算失败')
  }
}

async function rebaseline(row: Row) {
  try {
    const resp = await request(`${ENDPOINT}/${row.id}/rebaseline`, { method: 'POST' })
    const data = await resp.json()
    if (!data.ok) { flashError(data.message); return }
    flashOk(data.message)
    await refreshDetailFrom(row)
  } catch (e) {
    flashError(e instanceof Error ? e.message : '重新定基失败')
  }
}

async function recalculateAll() {
  recalculating.value = true
  try {
    const data = await (await request(`${ENDPOINT}/recalculate`, { method: 'POST' })).json()
    flashOk(data.message)
    await reload()
  } catch (e) {
    flashError(e instanceof Error ? e.message : '全部重算失败')
  } finally {
    recalculating.value = false
  }
}

async function activateVersion(version: string) {
  if (!version || version === ruleVersion.value) return
  try {
    const data = await (await request(`${ENDPOINT}/rules/activate`, {
      method: 'POST',
      body: JSON.stringify({ values: { version } }),
    })).json()
    if (!data.ok) { flashError(data.message); return }
    ruleVersion.value = version
    flashOk(data.message)
    await reload()
  } catch (e) {
    flashError(e instanceof Error ? e.message : '口径版本切换失败')
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const resp = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const data = await resp.json()
    if (!data.ok) { flashError(data.message); return }
    flashOk(data.message)
    await reload()
  } catch (e) {
    flashError(e instanceof Error ? e.message : '运输路线操作失败')
  }
}

onMounted(async () => {
  await loadMeta()
  await reload()
})
</script>

<style scoped>
.rule-bar {
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  background: #fff; border: 1px solid var(--border); border-radius: 8px;
  padding: 8px 12px; margin-bottom: 12px; font-size: 13px;
}
.rule-tag b { color: var(--brand); }
.rule-hint { color: var(--muted); font-size: 12px; flex: 1; min-width: 240px; }
.rule-switch { display: flex; align-items: center; gap: 6px; color: var(--muted); }
.route-table td.cell-note { max-width: 320px; color: var(--muted); font-size: 12px; }
.cell-waypoints { font-family: ui-monospace, Menlo, monospace; }
.row-deviated { background: #fef3f2; }
.row-noref { background: #f8fafc; }
.row-stale td { box-shadow: inset 3px 0 0 #f79009; }
.badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; margin-right: 4px; }
.badge-danger { background: #fee4e2; color: #b42318; }
.badge-warn { background: #fef0c7; color: #b54708; }
.badge-ok { background: #dcfae6; color: #067647; }
.badge-muted { background: #f2f4f7; color: #667085; }
.ver-badge { background: #eef4ff; color: #1f6feb; border-radius: 6px; padding: 1px 6px; font-size: 12px; }
.ok-text { color: #067647; }
.warn-text { color: #b54708; }
.muted { color: #98a2b3; }

.modal-mask {
  position: fixed; inset: 0; background: rgba(16, 24, 40, 0.45);
  display: flex; align-items: center; justify-content: center; z-index: 50; padding: 24px;
}
.modal {
  background: #fff; border-radius: 10px; padding: 20px 22px; width: 560px; max-width: 100%;
  max-height: 90vh; overflow: auto;
}
.modal-wide { width: 760px; }
.modal h3 { margin: 0 0 4px; }
.modal-sub { color: var(--muted); font-size: 12px; margin: 0 0 14px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.form-grid label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.form-grid .span-2 { grid-column: 1 / -1; }
.form-grid input, .form-grid select { padding: 7px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.field-hint { color: var(--muted); font-size: 12px; }
.preview-box {
  margin-top: 12px; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px;
  display: grid; grid-template-columns: 1fr 1fr; gap: 4px 16px; font-size: 13px; background: #f8fafc;
}
.preview-box > div:last-child, .preview-box .error-text, .preview-box .warn-text { grid-column: 1 / -1; }
.preview-warn { border-color: #f79009; background: #fffaeb; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.basis-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-bottom: 12px; }
.basis-grid > div { background: #f8fafc; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
.basis-grid span { display: block; color: var(--muted); font-size: 12px; }
.basis-empty { background: #f2f4f7; border-radius: 8px; padding: 10px; color: #b42318; font-size: 13px; }
.segment-table { margin-bottom: 12px; }
.deviation-box { background: #fef3f2; border: 1px solid #fecdca; color: #b42318; border-radius: 8px; padding: 8px 12px; font-size: 13px; margin-bottom: 12px; }
.recalc-box, .history-box { border: 1px dashed #f79009; border-radius: 8px; padding: 10px 12px; margin-bottom: 12px; font-size: 13px; }
.recalc-box h4, .history-box h4 { margin: 0 0 6px; font-size: 13px; }
.history-box ul { margin: 0; padding-left: 18px; color: var(--muted); }
</style>
