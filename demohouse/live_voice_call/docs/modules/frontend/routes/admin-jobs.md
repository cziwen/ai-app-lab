# frontend/src/routes/admin-jobs.tsx

## 模块职责
岗位管理后台页面，提供完整的岗位 CRUD 功能：
- 查看和搜索岗位列表
- 创建新岗位（上传 CSV 题库）
- 查看岗位详情（职责、要求、题库表格）
- 删除岗位记录

## 入口与调用方
- 路由路径：`/admin/jobs`
- 需要管理员身份验证（通过 `useAdminAuth` 守卫）
- 在 `AdminShell` 布局中渲染

## 对外接口（导出项）

### 组件定义
```typescript
export const AdminJobsPage: React.FC
```

## 核心功能

### 1. 岗位列表管理

#### 数据加载
```typescript
const loadJobs = async (query = jobSearch) => {
  setLoadingJobs(true)
  try {
    const data = await adminApi.listJobs(query)
    setJobs(data.items || [])
  } catch (e) {
    setGlobalError(e instanceof Error ? e.message : '加载岗位失败')
  } finally {
    setLoadingJobs(false)
  }
}
```

#### 列表数据结构
```typescript
interface JobListItem {
  job_uid: string           // 岗位唯一标识
  name: string              // 岗位名称
  question_count: number    // 题目总数
}
```

#### 搜索功能
- 支持模糊搜索：岗位名称、job_uid
- 实时更新列表：点击"搜索"按钮触发
- 搜索 API：`GET /api/admin/jobs?search={query}`

### 2. 创建岗位

#### 表单字段
```typescript
const [jobName, setJobName] = useState('')              // 岗位名称
const [jobDuties, setJobDuties] = useState('')          // 岗位职责
const [jobRequirements, setJobRequirements] = useState('') // 岗位要求
const [jobNotes, setJobNotes] = useState('')            // 补充说明（可选）
const [jobFile, setJobFile] = useState<File | null>(null) // 题库 CSV 文件
```

#### CSV 题库格式规范

##### 表头定义
```typescript
const CSV_TEMPLATE_COLUMNS = [
  '问题',
  '能力维度',
  '评分分界线',
  '最好标准',
  '中等标准',
  '最差标准',
  '输出格式'
] as const
```

##### CSV 文件示例
```csv
问题,能力维度,评分分界线,最好标准,中等标准,最差标准,输出格式
请介绍一下你的项目经验,项目经验,3分和7分,详细描述多个完整项目,仅描述单个项目,没有明确项目经验,文本
你如何处理团队冲突,团队协作,5分,主动化解并建立机制,能够妥善处理,回避或被动处理,文本
```

##### 编码要求
- 支持 UTF-8 编码（可带 BOM）
- 逗号分隔（支持引号包裹字段）
- 表头必须严格匹配（顺序和名称）

#### CSV 表头验证逻辑
```typescript
// 解析 CSV 首行（处理引号和逗号）
const parseHeaderLine = (line: string): string[] => {
  const cells: string[] = []
  let current = ''
  let inQuotes = false

  for (let i = 0; i < line.length; i++) {
    const char = line[i]

    if (char === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"'  // 转义的双引号
        i += 1
      } else {
        inQuotes = !inQuotes  // 切换引号状态
      }
      continue
    }

    if (char === ',' && !inQuotes) {
      cells.push(current.trim())
      current = ''
      continue
    }

    current += char
  }
  cells.push(current.trim())
  return cells
}

// 验证表头
const validateCsvHeader = async (file: File): Promise<string | null> => {
  const text = await file.text()
  if (!text.trim()) {
    return 'CSV 文件为空'
  }

  // 提取首行并去除 BOM
  const firstLine = text.split(/\r?\n/, 1)[0] || ''
  const normalizedLine = firstLine.replace(/^\uFEFF/, '')
  const actualColumns = parseHeaderLine(normalizedLine)

  // 比对表头
  const isMatch =
    actualColumns.length === CSV_TEMPLATE_COLUMNS.length &&
    CSV_TEMPLATE_COLUMNS.every((column, index) => actualColumns[index] === column)

  if (isMatch) {
    return null  // 验证通过
  }

  return `CSV 表头不匹配。期望: ${CSV_TEMPLATE_COLUMNS.join(',')}；实际: ${
    actualColumns.join(',') || '(空)'
  }`
}
```

#### 创建流程
```typescript
const handleCreateJob = async (event: FormEvent) => {
  event.preventDefault()
  setGlobalError('')

  // 1. 验证文件存在
  if (!jobFile) {
    setGlobalError('请上传题库 CSV')
    return
  }

  // 2. 验证 CSV 表头
  const headerError = await validateCsvHeader(jobFile)
  if (headerError) {
    setGlobalError(headerError)
    return
  }

  // 3. 构建 FormData
  const formData = new FormData()
  formData.append('name', jobName.trim())
  formData.append('duties', jobDuties.trim())
  formData.append('requirements', jobRequirements.trim())
  formData.append('notes', jobNotes.trim())
  formData.append('question_bank', jobFile)

  // 4. 提交创建请求
  setCreatingJob(true)
  try {
    await adminApi.createJob(formData)

    // 5. 重置表单并刷新列表
    setShowCreateJob(false)
    setJobName('')
    setJobDuties('')
    setJobRequirements('')
    setJobNotes('')
    setJobFile(null)
    await loadJobs('')
  } catch (e) {
    setGlobalError(e instanceof Error ? e.message : '创建岗位失败')
  } finally {
    setCreatingJob(false)
  }
}
```

#### CSV 上传字段
```tsx
<label htmlFor="job-csv">题库 CSV</label>
<input
  id="job-csv"
  type="file"
  accept=".csv,text/csv"
  onChange={event => setJobFile(event.target.files?.[0] || null)}
  required
/>
```

### 3. 岗位详情展示

#### 数据加载
```typescript
const openJobDetail = async (jobUid: string) => {
  setDetailLoading(true)
  setJobDetail(null)
  try {
    const data = await adminApi.getJob(jobUid)
    setJobDetail(data.job)
  } catch (e) {
    setGlobalError(e instanceof Error ? e.message : '加载岗位详情失败')
  } finally {
    setDetailLoading(false)
  }
}
```

#### 详情数据结构
```typescript
interface JobDetail {
  job_uid: string
  name: string
  duties: string           // 岗位职责
  requirements: string     // 岗位要求
  notes: string            // 补充说明
  questions: Array<{
    id: number
    question: string
    ability_dimension: string        // 能力维度
    scoring_boundary: string         // 评分分界线
    best_standard: string            // 最好标准
    reference_answer?: string        // 参考答案（兼容旧字段）
    medium_standard: string          // 中等标准
    worst_standard: string           // 最差标准
    output_format: string            // 输出格式
  }>
}
```

#### 详情展示区块

##### 1. 基本信息
```tsx
<h2 className="admin-detail-main-title">{jobDetail.name}</h2>
<p className="admin-detail-subtitle">岗位 UID: {jobDetail.job_uid}</p>

<section>
  <h3 className="admin-detail-title">职责</h3>
  <p>{jobDetail.duties}</p>
</section>

<section>
  <h3 className="admin-detail-title">要求</h3>
  <p>{jobDetail.requirements}</p>
</section>

<section>
  <h3 className="admin-detail-title">补充</h3>
  <p>{jobDetail.notes || '无'}</p>
</section>
```

##### 2. 题库表格
```tsx
<section>
  <h3 className="admin-detail-title">题库</h3>
  <div className="admin-table-wrap">
    <table className="admin-table">
      <thead>
        <tr>
          <th>#</th>
          <th>题目</th>
          <th>能力维度</th>
          <th>评分分界线</th>
          <th>最好标准</th>
          <th>中等标准</th>
          <th>最差标准</th>
          <th>输出格式</th>
        </tr>
      </thead>
      <tbody>
        {jobDetail.questions.map((item, index) => (
          <tr key={item.id}>
            <td>{index + 1}</td>
            <td>{item.question}</td>
            <td>{item.ability_dimension || '无'}</td>
            <td>{item.scoring_boundary || '无'}</td>
            <td>{item.best_standard || item.reference_answer || '无'}</td>
            <td>{item.medium_standard || '无'}</td>
            <td>{item.worst_standard || '无'}</td>
            <td>{item.output_format || '无'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
</section>
```

##### 题库表格说明
- **序号**：题目在题库中的顺序（从 1 开始）
- **题目**：面试问题文本
- **能力维度**：考察的能力方向（如：项目经验、团队协作、技术深度）
- **评分分界线**：分数等级划分（如：3分和7分）
- **最好标准**：优秀回答的标准（对应高分）
- **中等标准**：一般回答的标准（对应中等分数）
- **最差标准**：不及格回答的标准（对应低分）
- **输出格式**：答案期望格式（如：文本、结构化输出）

### 4. 删除岗位

#### 删除流程
```typescript
const handleDeleteJob = async (jobUid: string) => {
  // 1. 二次确认（提示关联影响）
  if (!window.confirm('确认删除该岗位？关联面试记录也会删除。')) {
    return
  }

  try {
    // 2. 调用删除 API
    await adminApi.deleteJob(jobUid)

    // 3. 关闭详情弹窗
    setJobDetail(null)

    // 4. 刷新列表
    await loadJobs()
  } catch (e) {
    setGlobalError(e instanceof Error ? e.message : '删除岗位失败')
  }
}
```

#### 注意事项
- 删除岗位会级联删除所有关联面试记录
- 操作不可逆，需二次确认
- 建议在删除前先确认无活跃面试

## 状态管理

### 全局状态
```typescript
const { loadingAuth, username, globalError, setGlobalError, handleLogout } =
  useAdminAuth()
```

### 列表状态
```typescript
const [jobSearch, setJobSearch] = useState('')
const [jobs, setJobs] = useState<JobListItem[]>([])
const [loadingJobs, setLoadingJobs] = useState(false)
```

### 创建表单状态
```typescript
const [showCreateJob, setShowCreateJob] = useState(false)
const [jobName, setJobName] = useState('')
const [jobDuties, setJobDuties] = useState('')
const [jobRequirements, setJobRequirements] = useState('')
const [jobNotes, setJobNotes] = useState('')
const [jobFile, setJobFile] = useState<File | null>(null)
const [creatingJob, setCreatingJob] = useState(false)
```

### 详情弹窗状态
```typescript
const [jobDetail, setJobDetail] = useState<JobDetail | null>(null)
const [detailLoading, setDetailLoading] = useState(false)
```

## UI 布局

### 页面结构
```tsx
<AdminShell
  activeTab="jobs"
  username={username}
  globalError={globalError}
  onLogout={handleLogout}
  toolbar={/* 搜索框 + 创建按钮 */}
>
  {/* 岗位列表卡片 */}
  <section className="admin-list-card">
    <h2>岗位列表</h2>
    <ul className="admin-list">
      {jobs.map(item => (
        <li key={item.job_uid}>
          {/* 岗位信息 + 操作按钮 */}
        </li>
      ))}
    </ul>
  </section>

  {/* 创建岗位弹窗 */}
  {showCreateJob && (
    <AdminModal title="创建岗位" onClose={...}>
      <form onSubmit={handleCreateJob}>
        {/* 表单字段 */}
      </form>
    </AdminModal>
  )}

  {/* 岗位详情弹窗 */}
  {(detailLoading || jobDetail) && (
    <AdminModal title="岗位详情" onClose={...}>
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
- `admin-detail-title`：详情区块标题
- `admin-table-wrap`：表格容器（支持横向滚动）
- `admin-table`：题库表格

## 依赖与配置

### 核心依赖
```typescript
import { FormEvent, useEffect, useState } from 'react'
import { adminApi, type JobDetail, type JobListItem } from '@/admin/api'
import { AdminLoadingPage, AdminModal, AdminShell } from '@/admin/layout'
import { useAdminAuth } from '@/admin/use-admin-auth'
```

### API 端点
- `GET /api/admin/jobs?search={query}`：列表查询
- `POST /api/admin/jobs`：创建岗位（FormData）
- `GET /api/admin/jobs/{job_uid}`：详情查询
- `DELETE /api/admin/jobs/{job_uid}`：删除岗位

### FormData 结构
```typescript
{
  name: string              // 岗位名称
  duties: string            // 职责
  requirements: string      // 要求
  notes: string             // 补充（可空）
  question_bank: File       // CSV 文件
}
```

## 日志与排障

### 日志输出
- 优先观察浏览器控制台
- 前端日志上报：`POST /api/frontend-logs`
- 错误捕获：全局错误通过 `setGlobalError` 展示在顶部

### 常见故障与排查步骤

#### 1. CSV 表头验证失败
**现象**：提示"CSV 表头不匹配"

**原因**：
- 表头列名拼写错误
- 列顺序不正确
- 包含多余空格或特殊字符
- 编码问题（非 UTF-8）

**排查**：
```typescript
// 检查实际表头
const file = document.querySelector('input[type="file"]').files[0]
const text = await file.text()
const firstLine = text.split(/\r?\n/, 1)[0].replace(/^\uFEFF/, '')
console.log('实际表头:', firstLine)
console.log('期望表头:', CSV_TEMPLATE_COLUMNS.join(','))
```

**解决**：
- 确保 CSV 首行为：`问题,能力维度,评分分界线,最好标准,中等标准,最差标准,输出格式`
- 使用 UTF-8 编码保存
- 避免表头字段前后有空格

#### 2. CSV 文件上传失败
**现象**：提交后提示"创建岗位失败"

**排查**：
- 检查文件大小：是否超过后端限制（通常 10MB）
- 检查文件类型：MIME type 是否为 `text/csv`
- 检查后端日志：CSV 解析错误、数据库插入错误

**解决**：
```typescript
// 验证文件信息
if (jobFile) {
  console.log('文件名:', jobFile.name)
  console.log('文件大小:', jobFile.size / 1024, 'KB')
  console.log('文件类型:', jobFile.type)
}
```

#### 3. 题库表格显示异常
**现象**：详情弹窗中题库表格错位或缺失字段

**排查**：
- 检查 `jobDetail.questions` 数据结构
- 检查字段映射：`best_standard` vs `reference_answer`
- 检查空值处理：`|| '无'`

**解决**：
```typescript
// 验证题库数据
console.log('题库数据:', jobDetail.questions)
console.log('第一题完整字段:', Object.keys(jobDetail.questions[0]))
```

#### 4. 删除岗位失败
**现象**：点击删除后无反应或提示错误

**排查**：
- 检查后端是否允许删除（有关联面试时）
- 检查权限：当前用户是否有删除权限
- 检查数据库约束：外键级联设置

**解决**：
```typescript
// 检查关联面试
const interviews = await adminApi.listInterviews('')
const linkedInterviews = interviews.items.filter(
  item => item.job.job_uid === jobUid
)
console.log('关联面试数:', linkedInterviews.length)
```

#### 5. 岗位列表加载失败
**现象**：提示"加载岗位失败"

**排查**：
- 检查网络请求：`GET /api/admin/jobs` 状态码
- 检查响应格式：是否包含 `items` 字段
- 检查数据库连接：后端日志

**解决**：
```typescript
// 重试加载
loadJobs('')
```

## 手工验证

### 完整测试流程

#### 1. CSV 文件准备
创建测试 CSV 文件 `test_questions.csv`：
```csv
问题,能力维度,评分分界线,最好标准,中等标准,最差标准,输出格式
请介绍一下你的项目经验,项目经验,3分和7分,详细描述多个完整项目,仅描述单个项目,没有明确项目经验,文本
你如何处理团队冲突,团队协作,5分,主动化解并建立机制,能够妥善处理,回避或被动处理,文本
描述一次技术难题的解决过程,问题解决,4分和8分,系统性分析并给出多种方案,能够解决但过程不清晰,未能有效解决,文本
```

#### 2. 登录验证
- 访问 `/admin/jobs`
- 确认自动跳转到登录页或正常显示

#### 3. 列表加载
- 页面加载后自动显示岗位列表
- 尝试搜索功能（输入岗位名称/UID）

#### 4. 创建岗位
- 点击"创建岗位"按钮
- 填写岗位名称：`前端工程师`
- 填写职责：`负责产品前端开发`
- 填写要求：`熟练掌握 React/Vue`
- 填写补充：`优先考虑有大型项目经验者`
- 上传题库 CSV
- 提交创建
- 确认列表刷新并显示新记录

#### 5. CSV 格式验证
- 上传错误表头的 CSV（如：`题目,维度,标准`）
- 确认提示"CSV 表头不匹配"
- 修正后重新上传

#### 6. 查看详情
- 点击"查看详情"按钮
- 确认基本信息显示正确
- 确认题库表格显示所有字段
- 验证表格横向滚动（字段较多时）

#### 7. 题库表格验证
- 检查序号列递增
- 检查所有题目正确显示
- 检查空字段显示"无"
- 测试表格滚动和缩放

#### 8. 删除岗位
- 点击"删除"按钮
- 确认弹出二次确认对话框
- 确认提示"关联面试记录也会删除"
- 确认删除
- 验证列表中记录消失

#### 9. 边界测试
- 创建岗位时不上传 CSV
- 上传空 CSV 文件
- 上传超大 CSV 文件（测试大小限制）
- 删除有关联面试的岗位
- CSV 包含特殊字符（引号、逗号、换行）

#### 10. 中文编码测试
- CSV 包含中文字符
- 测试带 BOM 的 UTF-8 文件
- 测试 GBK 编码文件（应失败或乱码）

## CSV 格式最佳实践

### 1. 使用标准编码
```bash
# 转换为 UTF-8
iconv -f GBK -t UTF-8 questions_gbk.csv > questions_utf8.csv
```

### 2. 处理特殊字符
```csv
"包含逗号,的题目",能力维度,评分分界线,"包含""引号""的标准",中等标准,最差标准,文本
```

### 3. 避免空行和空白
```csv
问题,能力维度,评分分界线,最好标准,中等标准,最差标准,输出格式
题目1,维度1,3分,标准1,标准2,标准3,文本
题目2,维度2,5分,标准1,标准2,标准3,文本
```

### 4. 使用工具验证
- Excel：另存为 -> CSV UTF-8（逗号分隔）
- VSCode：右下角查看编码，确保为 UTF-8
- 在线工具：CSVLint、CSV Validator

## 相关文档
- [管理后台认证](../admin/use-admin-auth.md)
- [管理后台 API](../admin/api.md)
- [面试管理页面](./admin-interviews.md)
- [CSV 题库导入后端实现](../../backend/routes/admin_jobs.md)
