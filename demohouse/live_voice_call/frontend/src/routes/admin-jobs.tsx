import { FormEvent, useEffect, useState } from 'react';
import { adminApi, type JobDetail, type JobListItem } from '@/admin/api';
import { AdminLoadingPage, AdminModal, AdminShell } from '@/admin/layout';
import { useAdminAuth } from '@/admin/use-admin-auth';

const CSV_TEMPLATE_COLUMNS = [
  '场景',
  '问题',
  '评分标准',
  '最大分数',
] as const;

const parseHeaderLine = (line: string): string[] => {
  const cells: string[] = [];
  let current = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];
    if (char === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"';
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }
    if (char === ',' && !inQuotes) {
      cells.push(current.trim());
      current = '';
      continue;
    }
    current += char;
  }
  cells.push(current.trim());
  return cells;
};

const validateCsvHeader = async (file: File): Promise<string | null> => {
  const text = await file.text();
  if (!text.trim()) {
    return 'CSV 文件为空';
  }
  const firstLine = text.split(/\r?\n/, 1)[0] || '';
  const normalizedLine = firstLine.replace(/^\uFEFF/, '');
  const actualColumns = parseHeaderLine(normalizedLine);
  const isMatch =
    actualColumns.length === CSV_TEMPLATE_COLUMNS.length &&
    CSV_TEMPLATE_COLUMNS.every((column, index) => actualColumns[index] === column);
  if (isMatch) {
    return null;
  }

  return `CSV 表头不匹配。期望: ${CSV_TEMPLATE_COLUMNS.join(',')}；实际: ${
    actualColumns.join(',') || '(空)'
  }`;
};

export const AdminJobsPage = () => {
  const { loadingAuth, username, globalError, setGlobalError, handleLogout } = useAdminAuth();
  const [jobSearch, setJobSearch] = useState('');
  const [jobs, setJobs] = useState<JobListItem[]>([]);
  const [loadingJobs, setLoadingJobs] = useState(false);

  const [showCreateJob, setShowCreateJob] = useState(false);
  const [jobModalOpen, setJobModalOpen] = useState(false);
  const [jobModalMode, setJobModalMode] = useState<'detail' | 'edit'>('detail');
  const [jobDetail, setJobDetail] = useState<JobDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [jobName, setJobName] = useState('');
  const [jobDuties, setJobDuties] = useState('');
  const [jobRequirements, setJobRequirements] = useState('');
  const [jobNotes, setJobNotes] = useState('');
  const [jobFile, setJobFile] = useState<File | null>(null);
  const [creatingJob, setCreatingJob] = useState(false);
  const [editJobName, setEditJobName] = useState('');
  const [editJobDuties, setEditJobDuties] = useState('');
  const [editJobRequirements, setEditJobRequirements] = useState('');
  const [editJobNotes, setEditJobNotes] = useState('');
  const [editJobFile, setEditJobFile] = useState<File | null>(null);
  const [updatingJob, setUpdatingJob] = useState(false);

  const loadJobs = async (query = jobSearch) => {
    setLoadingJobs(true);
    try {
      const data = await adminApi.listJobs(query);
      setJobs(data.items || []);
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : '加载岗位失败');
    } finally {
      setLoadingJobs(false);
    }
  };

  useEffect(() => {
    if (loadingAuth) {
      return;
    }
    loadJobs('');
  }, [loadingAuth]);

  const openJobDetail = async (jobUid: string) => {
    setJobModalOpen(true);
    setJobModalMode('detail');
    setDetailLoading(true);
    setJobDetail(null);
    try {
      const data = await adminApi.getJob(jobUid);
      setJobDetail(data.job);
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : '加载岗位详情失败');
    } finally {
      setDetailLoading(false);
    }
  };

  const handleDeleteJob = async (jobUid: string) => {
    if (!window.confirm('确认删除该岗位？若已有面试记录将无法删除。')) {
      return;
    }
    try {
      await adminApi.deleteJob(jobUid);
      setJobModalOpen(false);
      setJobModalMode('detail');
      setJobDetail(null);
      await loadJobs();
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : '删除岗位失败');
    }
  };

  const handleCreateJob = async (event: FormEvent) => {
    event.preventDefault();
    setGlobalError('');
    if (!jobFile) {
      setGlobalError('请上传题库 CSV');
      return;
    }

    const headerError = await validateCsvHeader(jobFile);
    if (headerError) {
      setGlobalError(headerError);
      return;
    }

    const formData = new FormData();
    formData.append('name', jobName.trim());
    formData.append('duties', jobDuties.trim());
    formData.append('requirements', jobRequirements.trim());
    formData.append('notes', jobNotes.trim());
    formData.append('question_bank', jobFile);

    setCreatingJob(true);
    try {
      await adminApi.createJob(formData);
      setShowCreateJob(false);
      setJobName('');
      setJobDuties('');
      setJobRequirements('');
      setJobNotes('');
      setJobFile(null);
      await loadJobs('');
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : '创建岗位失败');
    } finally {
      setCreatingJob(false);
    }
  };

  const openEditJobModal = () => {
    if (!jobDetail) {
      return;
    }
    setEditJobName(jobDetail.name);
    setEditJobDuties(jobDetail.duties);
    setEditJobRequirements(jobDetail.requirements);
    setEditJobNotes(jobDetail.notes || '');
    setEditJobFile(null);
    setJobModalMode('edit');
  };

  const closeJobModal = () => {
    setJobModalOpen(false);
    setJobModalMode('detail');
    setJobDetail(null);
    setEditJobName('');
    setEditJobDuties('');
    setEditJobRequirements('');
    setEditJobNotes('');
    setEditJobFile(null);
  };

  const handleUpdateJob = async (event: FormEvent) => {
    event.preventDefault();
    setGlobalError('');
    if (!jobDetail) {
      return;
    }

    if (editJobFile) {
      const headerError = await validateCsvHeader(editJobFile);
      if (headerError) {
        setGlobalError(headerError);
        return;
      }
    }

    const formData = new FormData();
    formData.append('name', editJobName.trim());
    formData.append('duties', editJobDuties.trim());
    formData.append('requirements', editJobRequirements.trim());
    formData.append('notes', editJobNotes.trim());
    formData.append('expected_updated_at', jobDetail.updated_at);
    if (editJobFile) {
      formData.append('question_bank', editJobFile);
    }

    setUpdatingJob(true);
    try {
      const data = await adminApi.updateJob(jobDetail.job_uid, formData);
      setJobDetail(data.job);
      setJobModalMode('detail');
      setEditJobFile(null);
      await loadJobs();
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : '更新岗位失败');
    } finally {
      setUpdatingJob(false);
    }
  };

  if (loadingAuth) {
    return <AdminLoadingPage />;
  }

  return (
    <AdminShell
      activeTab="jobs"
      username={username}
      globalError={globalError}
      onLogout={handleLogout}
      toolbar={
        <div className="admin-action-row">
          <input
            value={jobSearch}
            onChange={event => setJobSearch(event.target.value)}
            placeholder="搜索岗位名或 UID"
          />
          <button type="button" onClick={() => loadJobs(jobSearch)}>
            搜索
          </button>
          <button type="button" className="admin-primary-btn" onClick={() => setShowCreateJob(true)}>
            创建岗位
          </button>
        </div>
      }
    >
      <section className="admin-list-card">
        <h2>岗位列表</h2>
        {loadingJobs && <p className="admin-loading">加载中...</p>}
        <ul className="admin-list">
          {jobs.map(item => (
            <li key={item.job_uid}>
              <div>
                <strong>{item.name}</strong>
                <p>
                  UID: {item.job_uid} | 题目数: {item.question_count}
                </p>
              </div>
              <div className="admin-list-actions">
                <button type="button" onClick={() => openJobDetail(item.job_uid)}>
                  查看详情
                </button>
                <button type="button" onClick={() => handleDeleteJob(item.job_uid)}>
                  删除
                </button>
              </div>
            </li>
          ))}
          {!jobs.length && !loadingJobs && <li>暂无岗位</li>}
        </ul>
      </section>

      {showCreateJob && (
        <AdminModal title="创建岗位" onClose={() => setShowCreateJob(false)}>
          <form onSubmit={handleCreateJob}>
            <label htmlFor="job-name">岗位名称</label>
            <input
              id="job-name"
              value={jobName}
              onChange={event => setJobName(event.target.value)}
              required
            />

            <label htmlFor="job-duties">岗位描述 - 职责</label>
            <textarea
              id="job-duties"
              value={jobDuties}
              onChange={event => setJobDuties(event.target.value)}
              required
            />

            <label htmlFor="job-requirements">岗位描述 - 要求</label>
            <textarea
              id="job-requirements"
              value={jobRequirements}
              onChange={event => setJobRequirements(event.target.value)}
              required
            />

            <label htmlFor="job-notes">岗位描述 - 补充（可选）</label>
            <textarea
              id="job-notes"
              value={jobNotes}
              onChange={event => setJobNotes(event.target.value)}
            />

            <label htmlFor="job-csv">题库 CSV</label>
            <p className="admin-form-hint">
              规则：场景首问需填写“评分标准/最大分数”，同场景子问这两列必须留空（空值表示子问，不是继承）。
            </p>
            <input
              id="job-csv"
              type="file"
              accept=".csv,text/csv"
              onChange={event => setJobFile(event.target.files?.[0] || null)}
              required
            />

            <div className="admin-modal-actions">
              <button type="button" onClick={() => setShowCreateJob(false)}>
                取消
              </button>
              <button
                type="submit"
                disabled={
                  creatingJob ||
                  !jobName.trim() ||
                  !jobDuties.trim() ||
                  !jobRequirements.trim() ||
                  !jobFile
                }
              >
                {creatingJob ? '创建中...' : '提交创建'}
              </button>
            </div>
          </form>
        </AdminModal>
      )}

      {jobModalOpen && (
        <AdminModal
          title={jobModalMode === 'detail' ? '岗位详情' : '编辑岗位'}
          onClose={closeJobModal}
        >
          {detailLoading && <p className="admin-loading">加载详情中...</p>}
          {!detailLoading && jobDetail && jobModalMode === 'detail' && (
            <article className="admin-detail-article">
              <h2 className="admin-detail-main-title">{jobDetail.name}</h2>
              <p className="admin-detail-subtitle">岗位 UID: {jobDetail.job_uid}</p>
              <p className="admin-detail-subtitle">题库版本: v{jobDetail.question_bank_version}</p>
              <div className="admin-list-actions">
                <button type="button" onClick={openEditJobModal}>
                  编辑岗位
                </button>
                <button type="button" onClick={() => handleDeleteJob(jobDetail.job_uid)}>
                  删除岗位
                </button>
              </div>

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

              <section>
                <h3 className="admin-detail-title">题库</h3>
                <div className="admin-table-wrap">
                  <table className="admin-table">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>场景</th>
                        <th>类型</th>
                        <th>题目</th>
                        <th>评分标准</th>
                        <th>最大分数</th>
                      </tr>
                    </thead>
                    <tbody>
                      {jobDetail.questions.map((item, index) => (
                        <tr key={item.id}>
                          <td>{index + 1}</td>
                          <td>{item.scenario || '无'}</td>
                          <td>{item.score_format ? '场景首问' : '场景子问'}</td>
                          <td>{item.question}</td>
                          <td>{item.scoring_boundary || '无'}</td>
                          <td>{item.score_format || '无'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </article>
          )}
          {!detailLoading && jobDetail && jobModalMode === 'edit' && (
            <form onSubmit={handleUpdateJob}>
              <label htmlFor="edit-job-name">岗位名称</label>
              <input
                id="edit-job-name"
                value={editJobName}
                onChange={event => setEditJobName(event.target.value)}
                required
              />

              <label htmlFor="edit-job-duties">岗位描述 - 职责</label>
              <textarea
                id="edit-job-duties"
                value={editJobDuties}
                onChange={event => setEditJobDuties(event.target.value)}
                required
              />

              <label htmlFor="edit-job-requirements">岗位描述 - 要求</label>
              <textarea
                id="edit-job-requirements"
                value={editJobRequirements}
                onChange={event => setEditJobRequirements(event.target.value)}
                required
              />

              <label htmlFor="edit-job-notes">岗位描述 - 补充（可选）</label>
              <textarea
                id="edit-job-notes"
                value={editJobNotes}
                onChange={event => setEditJobNotes(event.target.value)}
              />

              <label htmlFor="edit-job-csv">覆盖题库 CSV（可选）</label>
              <p className="admin-form-hint">
                不上传则仅更新岗位信息；上传后会生成新题库版本，仅影响后续新建面试。
              </p>
              <input
                id="edit-job-csv"
                type="file"
                accept=".csv,text/csv"
                onChange={event => setEditJobFile(event.target.files?.[0] || null)}
              />

              <div className="admin-modal-actions">
                <button type="button" onClick={() => setJobModalMode('detail')}>
                  返回详情
                </button>
                <button
                  type="submit"
                  disabled={
                    updatingJob || !editJobName.trim() || !editJobDuties.trim() || !editJobRequirements.trim()
                  }
                >
                  {updatingJob ? '保存中...' : '保存修改'}
                </button>
              </div>
            </form>
          )}
        </AdminModal>
      )}
    </AdminShell>
  );
};
