# frontend/src/routes/admin-interviews.tsx

## 模块职责
面试管理后台页面，提供完整的面试记录 CRUD 功能：
- 查看和搜索面试列表
- 创建新面试（配置候选人、岗位、题目追问次数、必检项）
- 查看面试详情（基本信息、对话记录、音频播放）
- 删除面试记录

## 入口与调用方
- 路由路径：`/admin/interviews`
- 需要管理员身份验证（通过 `useAdminAuth` 守卫）
- 在 `AdminShell` 布局中渲染

## 对外接口（导出项）

### 组件定义
```typescript
export const AdminInterviewsPage: React.FC
```

## 核心功能

### 1. 面试列表管理

#### 数据加载
```typescript
const loadInterviews = async (query = interviewSearch) => {
  setLoadingInterviews(true)
  try {
    const data = await adminApi.listInterviews(query)
    setInterviews(data.items || [])
  } catch (e) {
    setGlobalError(e instanceof Error ? e.message : '加载面试失败')
  } finally {
    setLoadingInterviews(false)
  }
}
```

#### 列表数据结构
```typescript
interface InterviewListItem {
  token: string              // 面试唯一标识
  candidate_name: string     // 候选人姓名
  job: {
    name: string            // 岗位名称
  }
  status: string            // 状态：pending/completed/cancelled
}
```

#### 搜索功能
- 支持模糊搜索：候选人姓名、岗位名、token
- 实时更新列表：点击"搜索"按钮触发
- 搜索 API：`GET /api/admin/interviews?search={query}`

### 2. 创建面试

#### 表单字段
```typescript
const [candidateName, setCandidateName] = useState('')          // 候选人姓名
const [selectedJobUid, setSelectedJobUid] = useState('')        // 岗位 UID
const [questionFollowupInputs, setQuestionFollowupInputs] =     // 题目追问配置
  useState<Record<number, string>>({})
const [interviewNotes, setInterviewNotes] = useState('')        // 备注
const [requiredCheckins, setRequiredCheckins] =                 // 必检项
  useState<CheckInKey[]>(['speaker', 'mic'])
```

#### 创建流程
```typescript
const handleCreateInterview = async (event: FormEvent) => {
  event.preventDefault()
  setGlobalError('')
  setCreatingInterview(true)

  try {
    // 1. 验证追问次数（0-3 整数）
    const question_followups = selectedJobQuestions.map(question => {
      const raw = questionFollowupInputs[question.id] ?? '0'
      const parsed = Number.parseInt(raw, 10)
      if (!Number.isFinite(parsed) || Number.isNaN(parsed) ||
          parsed < 0 || parsed > 3) {
        throw new Error(`题目「${question.question}」的追问次数必须是 0-3 的整数`)
      }
      return {
        question_id: question.id,
        max_followups: parsed
      }
    })

    // 2. 提交创建请求
    await adminApi.createInterview({
      candidate_name: candidateName.trim(),
      job_uid: selectedJobUid,
      question_followups,
      notes: interviewNotes.trim(),
      required_checkins: requiredCheckins
    })

    // 3. 重置表单并刷新列表
    setShowCreateInterview(false)
    setCandidateName('')
    setInterviewNotes('')
    setRequiredCheckins(['speaker', 'mic'])
    await loadInterviews('')
  } catch (e) {
    setGlobalError(e instanceof Error ? e.message : '创建面试失败')
  } finally {
    setCreatingInterview(false)
  }
}
```

#### 必检项配置
```typescript
type CheckInKey = 'speaker' | 'mic' | 'camera' | 'screen'

const CHECKIN_OPTIONS: Array<{ key: CheckInKey; label: string }> = [
  { key: 'speaker', label: '扬声器' },
  { key: 'mic', label: '麦克风' },
  { key: 'camera', label: '摄像头' },
  { key: 'screen', label: '屏幕共享' }
]

// 默认勾选扬声器和麦克风
// 未勾选项不会出现在候选人 check-in 流程中
```

#### 题目追问次数
- 动态加载：根据选中岗位自动拉取题库
- 逐题配置：每道题独立设置追问次数（0-3）
- 验证规则：必须为非负整数且不超过 3
- UI 展示：输入框 `type="number" min={0} max={3}`

### 3. 面试详情展示

#### 数据加载
```typescript
const openInterviewDetail = async (token: string) => {
  setDetailLoading(true)
  setInterviewDetail(null)
  try {
    const data = await adminApi.getInterview(token)
    setInterviewDetail(data.interview)
  } catch (e) {
    setGlobalError(e instanceof Error ? e.message : '加载面试详情失败')
  } finally {
    setDetailLoading(false)
  }
}
```

#### 详情数据结构
```typescript
interface InterviewDetail {
  token: string
  candidate_name: string
  status: 'pending' | 'completed' | 'cancelled'
  job: {
    name: string
    job_uid: string
  }
  question_count: number
  created_at: string             // ISO 8601 时间
  completed_at: string | null
  interview_link: string         // 候选人入口链接
  notes: string
  required_checkins: CheckInKey[]
  selected_questions: Array<{
    sort_order: number
    question: string
    max_followups: number
  }>
  completed: boolean
  turns: Array<{                  // 对话记录
    role: 'candidate' | 'interviewer'
    content: string
    created_at: string
  }>
  audio: {                        // 音频文件
    candidate_url: string         // 候选人音轨相对路径
    interviewer_url: string       // 面试官音轨相对路径
  }
  completion_message?: string
}
```

#### 详情展示区块

##### 1. 基本信息
```tsx
<section className="admin-detail-grid">
  <p>岗位：{interviewDetail.job.name}</p>
  <p>岗位 UID：{interviewDetail.job.job_uid}</p>
  <p>题目数：{interviewDetail.question_count}</p>
  <p>创建时间：{interviewDetail.created_at}</p>
  <p>完成时间：{interviewDetail.completed_at || '未完成'}</p>
</section>
```

##### 2. 面试链接
```tsx
<section>
  <h3>面试链接</h3>
  <a href={interviewDetail.interview_link} target="_blank" rel="noreferrer">
    {interviewDetail.interview_link}
  </a>
</section>
```

##### 3. 必检项
```tsx
<section>
  <h3>必检项</h3>
  <p>
    {interviewDetail.required_checkins?.length
      ? interviewDetail.required_checkins
          .map(item => CHECKIN_LABEL[item] || item)
          .join(' / ')
      : '无（本场无需设备检查）'}
  </p>
</section>
```

##### 4. 抽中问题
```tsx
<section>
  <h3>抽中问题</h3>
  <ol className="admin-qa-list">
    {interviewDetail.selected_questions.map(item => (
      <li key={`${item.sort_order}-${item.question}`}>
        {item.question}（追问上限: {item.max_followups}）
      </li>
    ))}
  </ol>
</section>
```

##### 5. 对话记录（仅完成后）
```tsx
{interviewDetail.completed && (
  <section>
    <h3>对话记录</h3>
    <ul className="admin-turn-list">
      {(interviewDetail.turns || []).map((turn, index) => (
        <li key={`${turn.created_at}-${index}`}>
          <strong>{turn.role === 'candidate' ? '候选人' : '面试官'}：</strong>
          {turn.content}
        </li>
      ))}
    </ul>
  </section>
)}
```

##### 6. 音频播放（仅完成后）
```tsx
{interviewDetail.completed && (
  <section>
    <h3>音频</h3>
    <div className="admin-audio-row">
      <div>
        <p>候选人音轨</p>
        <audio
          controls
          src={`${API_URL}${interviewDetail.audio?.candidate_url || ''}`}
        />
      </div>
      <div>
        <p>面试官音轨</p>
        <audio
          controls
          src={`${API_URL}${interviewDetail.audio?.interviewer_url || ''}`}
        />
      </div>
    </div>
  </section>
)}
```

### 4. 删除面试

#### 删除流程
```typescript
const handleDeleteInterview = async (token: string) => {
  // 1. 二次确认
  if (!window.confirm('确认删除该面试记录？')) {
    return
  }

  try {
    // 2. 调用删除 API
    await adminApi.deleteInterview(token)

    // 3. 关闭详情弹窗
    setInterviewDetail(null)

    // 4. 刷新列表
    await loadInterviews()
  } catch (e) {
    setGlobalError(e instanceof Error ? e.message : '删除面试失败')
  }
}
```

#### 注意事项
- 删除操作不可逆
- 会同时删除关联的音频文件和对话记录
- 需要二次确认避免误操作

## 状态管理

### 全局状态
```typescript
const { loadingAuth, username, globalError, setGlobalError, handleLogout } =
  useAdminAuth()
```

### 列表状态
```typescript
const [interviewSearch, setInterviewSearch] = useState('')
const [interviews, setInterviews] = useState<InterviewListItem[]>([])
const [loadingInterviews, setLoadingInterviews] = useState(false)
```

### 创建表单状态
```typescript
const [showCreateInterview, setShowCreateInterview] = useState(false)
const [candidateName, setCandidateName] = useState('')
const [selectedJobUid, setSelectedJobUid] = useState('')
const [selectedJobQuestions, setSelectedJobQuestions] = useState<
  Array<{ id: number; question: string }>
>([])
const [questionFollowupInputs, setQuestionFollowupInputs] =
  useState<Record<number, string>>({})
const [interviewNotes, setInterviewNotes] = useState('')
const [requiredCheckins, setRequiredCheckins] =
  useState<CheckInKey[]>(['speaker', 'mic'])
const [creatingInterview, setCreatingInterview] = useState(false)
```

### 详情弹窗状态
```typescript
const [interviewDetail, setInterviewDetail] = useState<InterviewDetail | null>(null)
const [detailLoading, setDetailLoading] = useState(false)
```

### 岗位数据（用于创建）
```typescript
const [jobs, setJobs] = useState<JobListItem[]>([])
```

## 数据流

### 初始化流程
```
1. useAdminAuth 验证身份 -> 获取 username
2. 并行加载：
   - loadJobs() -> 填充岗位下拉列表
   - loadInterviews('') -> 填充面试列表
```

### 岗位选择联动
```typescript
useEffect(() => {
  const loadSelectedJobQuestions = async () => {
    if (!selectedJobUid) {
      setSelectedJobQuestions([])
      setQuestionFollowupInputs({})
      return
    }

    try {
      const data = await adminApi.getJob(selectedJobUid)
      const questions = (data.job.questions || []).map(item => ({
        id: item.id,
        question: item.question
      }))
      setSelectedJobQuestions(questions)

      // 初始化追问次数为 0
      setQuestionFollowupInputs(
        questions.reduce<Record<number, string>>((acc, question) => {
          acc[question.id] = '0'
          return acc
        }, {})
      )
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : '加载岗位题目失败')
    }
  }
  loadSelectedJobQuestions()
}, [selectedJobUid])
```

## UI 布局

### 页面结构
```tsx
<AdminShell
  activeTab="interviews"
  username={username}
  globalError={globalError}
  onLogout={handleLogout}
  toolbar={/* 搜索框 + 创建按钮 */}
>
  {/* 面试列表卡片 */}
  <section className="admin-list-card">
    <h2>面试列表</h2>
    <ul className="admin-list">
      {interviews.map(item => (
        <li key={item.token}>
          {/* 候选人信息 + 操作按钮 */}
        </li>
      ))}
    </ul>
  </section>

  {/* 创建面试弹窗 */}
  {showCreateInterview && (
    <AdminModal title="创建面试" onClose={...}>
      <form onSubmit={handleCreateInterview}>
        {/* 表单字段 */}
      </form>
    </AdminModal>
  )}

  {/* 面试详情弹窗 */}
  {(detailLoading || interviewDetail) && (
    <AdminModal title="面试详情" onClose={...}>
      <article className="admin-detail-article">
        {/* 详情区块 */}
      </article>
    </AdminModal>
  )}
</AdminShell>
```

### 关键样式类
- `admin-list-card`：列表卡片容器
- `admin-list`：列表项容器
- `admin-list-actions`：操作按钮组
- `admin-modal-actions`：弹窗底部按钮组
- `admin-detail-article`：详情文章容器
- `admin-detail-grid`：详情网格布局
- `admin-detail-title`：详情区块标题
- `admin-qa-list`：题目列表
- `admin-turn-list`：对话记录列表
- `admin-audio-row`：音频播放器行布局
- `admin-checkin-grid`：必检项复选框网格
- `admin-followup-row`：追问次数输入行

## 依赖与配置

### 核心依赖
```typescript
import { FormEvent, useEffect, useState } from 'react'
import { API_URL } from '@/config/endpoints'
import {
  adminApi,
  type CheckInKey,
  type InterviewDetail,
  type InterviewListItem,
  type JobListItem
} from '@/admin/api'
import { AdminLoadingPage, AdminModal, AdminShell } from '@/admin/layout'
import { useAdminAuth } from '@/admin/use-admin-auth'
```

### API 端点
- `GET /api/admin/interviews?search={query}`：列表查询
- `POST /api/admin/interviews`：创建面试
- `GET /api/admin/interviews/{token}`：详情查询
- `DELETE /api/admin/interviews/{token}`：删除面试
- `GET /api/admin/jobs`：岗位列表
- `GET /api/admin/jobs/{job_uid}`：岗位详情（含题库）

### 音频播放
- 音频文件存储：`/api/public/audio/{filename}`
- 双音轨分离：候选人音轨 + 面试官音轨
- 浏览器原生控件：`<audio controls />`
- 支持格式：MP3/WAV（取决于后端配置）

## 日志与排障

### 日志输出
- 优先观察浏览器控制台
- 前端日志上报：`POST /api/frontend-logs`
- 错误捕获：全局错误通过 `setGlobalError` 展示在顶部

### 常见故障与排查步骤

#### 1. 页面空白或跳转异常
**现象**：页面无内容或自动跳转

**排查**：
- 检查 `useAdminAuth` 是否返回有效 `username`
- 检查浏览器 localStorage 中是否有 `admin_token`
- 检查 `/api/admin/auth/verify` 是否返回 200

**解决**：
```typescript
// 重新登录
handleLogout()
// 或手动设置 token
localStorage.setItem('admin_token', 'your_token')
```

#### 2. 列表加载失败
**现象**：提示"加载面试失败"

**排查**：
- 检查网络请求：`GET /api/admin/interviews` 状态码
- 检查响应格式：是否包含 `items` 字段
- 检查数据库连接：后端日志

**解决**：
```typescript
// 重试加载
loadInterviews('')
```

#### 3. 创建面试失败
**现象**：提交后提示错误

**排查**：
- 检查表单验证：候选人姓名、岗位、题目追问次数
- 检查后端日志：数据库插入错误、题库不存在
- 检查岗位题库：`selectedJobQuestions.length === 0` 时禁用提交

**解决**：
```typescript
// 确保岗位有题库
if (selectedJobQuestions.length === 0) {
  setGlobalError('当前岗位题库为空，无法创建面试')
  return
}
```

#### 4. 音频播放失败
**现象**：音频控件显示但无法播放

**排查**：
- 检查音频 URL：`${API_URL}${interviewDetail.audio?.candidate_url}`
- 检查文件存在：访问 URL 是否返回 404
- 检查文件格式：浏览器是否支持（MP3/WAV）
- 检查 CORS：音频资源是否允许跨域

**解决**：
```typescript
// 验证 URL 完整性
console.log('音频 URL:', `${API_URL}${interviewDetail.audio?.candidate_url}`)
// 手动访问 URL 确认文件可访问
```

#### 5. 详情弹窗无数据
**现象**：弹窗打开但显示"加载详情中..."一直不结束

**排查**：
- 检查 `openInterviewDetail` 是否被调用
- 检查 `adminApi.getInterview(token)` 是否抛出异常
- 检查 `detailLoading` 状态是否正确重置

**解决**：
```typescript
// 添加超时处理
const controller = new AbortController()
setTimeout(() => controller.abort(), 10000)
const data = await adminApi.getInterview(token, { signal: controller.signal })
```

#### 6. 必检项配置不生效
**现象**：候选人 check-in 页面未按配置显示

**排查**：
- 检查 `required_checkins` 数组是否正确传递
- 检查后端是否正确存储和返回该字段
- 检查 check-in 页面是否正确读取配置

**解决**：
```typescript
// 验证配置传递
console.log('必检项配置:', requiredCheckins)
// 确认 API 响应
const data = await adminApi.getInterview(token)
console.log('后端返回必检项:', data.interview.required_checkins)
```

## 手工验证

### 完整测试流程
1. **登录验证**
   - 访问 `/admin/interviews`
   - 确认自动跳转到登录页或正常显示

2. **列表加载**
   - 页面加载后自动显示面试列表
   - 尝试搜索功能（输入候选人姓名/岗位/token）

3. **创建面试**
   - 点击"创建面试"按钮
   - 填写候选人姓名
   - 选择岗位（自动加载题库）
   - 配置每题追问次数（0-3）
   - 勾选必检项
   - 提交创建
   - 确认列表刷新并显示新记录

4. **查看详情**
   - 点击"查看详情"按钮
   - 确认基本信息显示正确
   - 复制面试链接，验证可访问
   - 确认抽中问题列表显示
   - 如已完成，确认对话记录和音频播放

5. **音频播放**
   - 点击候选人音轨播放按钮
   - 确认音频正常播放
   - 点击面试官音轨播放按钮
   - 确认音频正常播放
   - 测试暂停/进度控制

6. **删除面试**
   - 点击"删除"按钮
   - 确认弹出二次确认对话框
   - 确认删除
   - 验证列表中记录消失

7. **边界测试**
   - 创建面试时不选择岗位
   - 追问次数输入负数/超过 3
   - 删除后尝试再次访问详情
   - 未完成的面试查看详情（无对话记录/音频）

## 相关文档
- [管理后台认证](../admin/use-admin-auth.md)
- [管理后台 API](../admin/api.md)
- [岗位管理页面](./admin-jobs.md)
- [候选人入口页面](./check-in.md)
