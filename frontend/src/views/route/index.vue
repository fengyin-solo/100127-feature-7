<template>
  <section class="page" data-module="route">
    <header class="page-head">
      <div>
        <h2>运输路线管理</h2>
        <p class="page-desc">
          预计里程、预计耗时与过路费用按口径 {{ profile?.版本 ?? '…' }} 自动计算，
          途经节点用「&gt;」分隔；里程超上限会提示偏离原因，里程依据留档供复核。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记路线方案</button>
        <button class="btn" type="button" @click="recalculateAll">批量重算口径</button>
        <button class="btn" type="button" @click="exportRows">导出运输路线清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>路线编号</span>
        <input v-model="keyword" placeholder="按路线编号检索" />
      </label>
      <label class="filter-item">
        <span>路线状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column" :class="cellClass(column, row)">
            <span :title="column === '预计里程' ? String(row['里程超限提示'] || '') : ''">
              {{ formatCell(column, row) }}
            </span>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="openBasis(row)">里程依据</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无运输路线数据，可先登记路线方案</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条运输路线记录</span>
      <span v-if="infoMessage" class="info-text">{{ infoMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="createVisible" class="modal-mask" @click.self="createVisible = false">
      <div class="modal-panel">
        <h3 class="modal-title">登记路线方案</h3>
        <div class="form-grid">
          <label class="form-item">
            <span>路线编号 *</span>
            <input v-model.trim="form.路线编号" placeholder="如 ROUT-1001" />
          </label>
          <label class="form-item">
            <span>车型类别</span>
            <select v-model="form.车型类别">
              <option v-for="cls in vehicleClasses" :key="cls" :value="cls">{{ cls }}</option>
            </select>
          </label>
          <label class="form-item">
            <span>出发地 *</span>
            <input v-model.trim="form.出发地" placeholder="如 上海" />
          </label>
          <label class="form-item">
            <span>目的地 *</span>
            <input v-model.trim="form.目的地" placeholder="如 北京" />
          </label>
          <label class="form-item form-item-wide">
            <span>途经节点（{{ profile?.途经节点格式 ?? '节点1>节点2' }}）</span>
            <input v-model.trim="form.途经节点" placeholder="如 南京>徐州>济南" />
          </label>
        </div>

        <div v-if="preview" class="preview-box" :class="preview.kind">
          <template v-if="preview.kind !== 'error'">
            <strong>预计里程 {{ preview.预计里程 }} 公里 · 预计耗时 {{ preview.预计耗时 }} 小时 · 过路费约 {{ preview.过路费用 }} 元</strong>
            <p v-if="preview.说明" class="preview-note">{{ preview.说明 }}</p>
            <p v-if="preview.超限提示" class="preview-note warn-text">⚠ {{ preview.超限提示 }}</p>
          </template>
          <template v-else>
            {{ preview.reason }}
          </template>
        </div>
        <p v-else class="preview-hint">填写出发地、目的地与途经节点后，参考值会按口径自动算出并随登记一并保存。</p>

        <p v-if="createError" class="error-text">{{ createError }}</p>
        <div class="modal-actions">
          <button class="btn ghost" type="button" @click="createVisible = false">取消</button>
          <button class="btn primary" type="button" :disabled="submitting" @click="submitCreate">
            {{ submitting ? '登记中…' : '确认登记' }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="basisEntry" class="modal-mask" @click.self="basisEntry = null">
      <div class="modal-panel">
        <h3 class="modal-title">里程依据（复核视图）— {{ basisEntry['路线编号'] }}</h3>
        <template v-if="basisEntry['里程依据']">
          <table class="basis-table">
            <tbody>
              <tr><th>口径版本</th><td>{{ basis.口径版本 }}</td><th>计算时间</th><td>{{ basis.计算时间 }}</td></tr>
              <tr><th>口径键</th><td colspan="3">{{ basis.口径键 }}</td></tr>
              <tr><th>节点序列</th><td colspan="3">{{ (basis.节点序列 ?? []).join(' > ') }}</td></tr>
              <tr><th>耗时构成</th><td colspan="3">{{ basis.耗时构成 }}</td></tr>
              <tr>
                <th>车型类别</th><td>{{ basis.车型类别 }}（{{ basis.费率元每公里 }} 元/公里）</td>
                <th>过路费参考</th><td>{{ basisEntry['过路费用'] }} 元</td>
              </tr>
              <tr>
                <th>参考路径</th><td>{{ (basis.参考路径 ?? []).join(' > ') || '—' }}</td>
                <th>合理上限</th><td>{{ basis.参考里程公里 }} 公里 × {{ basis.上限系数 }} = {{ basis.合理上限公里 }} 公里</td>
              </tr>
            </tbody>
          </table>
          <table class="basis-table">
            <thead>
              <tr><th>区段</th><th>里程（公里）</th></tr>
            </thead>
            <tbody>
              <tr v-for="leg in basis.区段明细" :key="leg.区段">
                <td>{{ leg.区段 }}</td>
                <td>{{ leg.里程公里 }}</td>
              </tr>
              <tr><th>合计</th><th>{{ basis.里程公里 }}</th></tr>
            </tbody>
          </table>
        </template>
        <p v-else class="preview-box error">未生成参考值：{{ basisEntry['参考值说明'] || '原因未知' }}</p>
        <p v-if="basisEntry['里程超限提示']" class="preview-box warn">⚠ {{ basisEntry['里程超限提示'] }}</p>
        <p class="preview-hint">
          本依据为录入（或上次重算）时的快照，口径规则变更不影响此份内容；
          执行「重算口径」后按新口径重新生成。
        </p>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="basisEntry = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

type Profile = {
  版本: string
  平均时速公里: number
  途经停靠分钟: number
  上限系数: number
  费率表: Record<string, number>
  默认车型: string
  途经节点格式: string
}

type Preview =
  | { kind: 'ok'; 预计里程: number; 预计耗时: number; 过路费用: number; 超限提示: string; 说明: string }
  | { kind: 'warn'; 预计里程: number; 预计耗时: number; 过路费用: number; 超限提示: string; 说明: string }
  | { kind: 'error'; reason: string }

const ENDPOINT = '/api/route'
const columns = ["路线编号", "出发地", "目的地", "途经节点", "车型类别", "预计里程", "预计耗时", "过路费用", "口径版本", "路线状态"]
const actions = ["启用路线", "停用路线", "废弃路线", "重算口径"]
const statuses = ["可用", "不可用", "备选", "已废弃"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const infoMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const profile = ref<Profile | null>(null)

const createVisible = ref(false)
const submitting = ref(false)
const createError = ref('')
const form = reactive({ 路线编号: '', 出发地: '', 目的地: '', 途经节点: '', 车型类别: '' })
const preview = ref<Preview | null>(null)

const basisEntry = ref<Row | null>(null)
const basis = computed<Row>(() => (basisEntry.value?.['里程依据'] as Row) ?? {})

const vehicleClasses = computed(() => Object.keys(profile.value?.费率表 ?? {}))

const stats = computed(() => [
  { label: '路线总数', value: total.value },
  { label: '本页里程超限', value: rows.value.filter((row) => row['里程超限提示']).length },
  { label: '本页待重算', value: rows.value.filter((row) => row['口径版本'] !== profile.value?.版本).length },
  { label: '当前口径版本', value: profile.value?.版本 ?? '…' },
])

function formatCell(column: string, row: Row): string {
  const value = row[column]
  if (value === null || value === undefined || value === '') {
    if (['预计里程', '预计耗时', '过路费用'].includes(column)) return '未生成'
    return '—'
  }
  if (column === '预计里程') return `${value} 公里${row['里程超限提示'] ? ' ⚠' : ''}`
  if (column === '预计耗时') return `${value} 小时`
  if (column === '过路费用') return `${value} 元`
  return String(value)
}

function cellClass(column: string, row: Row): string {
  if (column === '预计里程' && row['里程超限提示']) return 'cell-over'
  return ''
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
  form.路线编号 = ''
  form.出发地 = ''
  form.目的地 = ''
  form.途经节点 = ''
  form.车型类别 = profile.value?.默认车型 ?? vehicleClasses.value[0] ?? ''
  createError.value = ''
  preview.value = null
  createVisible.value = true
}

let previewTimer: ReturnType<typeof setTimeout> | undefined
watch(
  () => [form.出发地, form.目的地, form.途经节点, form.车型类别],
  () => {
    if (!createVisible.value) return
    clearTimeout(previewTimer)
    previewTimer = setTimeout(() => void runPreview(), 400)
  },
)

async function runPreview() {
  if (!form.出发地 || !form.目的地) {
    preview.value = null
    return
  }
  try {
    const response = await request(`${ENDPOINT}/preview`, {
      method: 'POST',
      body: JSON.stringify({ values: { ...form } }),
    })
    const data = await response.json()
    if (!data.ok) {
      preview.value = { kind: 'error', reason: data.reason ?? '未生成参考值' }
      return
    }
    preview.value = {
      kind: data.超限提示 ? 'warn' : 'ok',
      预计里程: data.预计里程,
      预计耗时: data.预计耗时,
      过路费用: data.过路费用,
      超限提示: data.超限提示 ?? '',
      说明: data.说明 ?? '',
    }
  } catch (error) {
    preview.value = { kind: 'error', reason: error instanceof Error ? error.message : '试算失败' }
  }
}

async function submitCreate() {
  createError.value = ''
  submitting.value = true
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...form } }),
    })
    const data = await response.json()
    if (!data.ok) {
      createError.value = data.message ?? '登记失败'
      return
    }
    createVisible.value = false
    infoMessage.value = data.message ?? '路线方案已登记'
    await reload()
  } catch (error) {
    createError.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    submitting.value = false
  }
}

async function openBasis(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) throw new Error('里程依据读取失败')
    basisEntry.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '里程依据读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  infoMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const data = await response.json()
    if (!data.ok) throw new Error(data.message ?? '运输路线动作未生效')
    infoMessage.value = data.message ?? ''
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '运输路线操作失败'
  }
}

async function recalculateAll() {
  errorMessage.value = ''
  infoMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/recalculate`, { method: 'POST', body: '{}' })
    const data = await response.json()
    const parts = [
      `口径 ${data.口径版本}：重算 ${data.重算总数} 条`,
      `${data.已生成参考值} 条已生成参考值（超限 ${data.其中超限} 条）`,
      `${data.未生成参考值} 条未生成`,
    ]
    const failures = (data.失败明细 ?? []).map((item: Row) => `${item['路线编号']}：${item['原因']}`)
    infoMessage.value = failures.length ? `${parts.join('，')}；${failures.join('；')}` : parts.join('，')
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量重算失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) throw new Error('路线方案列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '运输路线列表读取失败'
  }
}

async function loadProfile() {
  try {
    const response = await request(`${ENDPOINT}/profile`)
    if (response.ok) profile.value = await response.json()
  } catch {
    profile.value = null
  }
}

onMounted(() => {
  void loadProfile()
  void reload()
})
</script>
