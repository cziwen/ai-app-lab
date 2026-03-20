import { FormEvent, useEffect, useState } from 'react';
import { adminApi, type JobDetail, type JobListItem } from '@/admin/api';
import { AdminLoadingPage, AdminModal, AdminShell } from '@/admin/layout';
import { useAdminAuth } from '@/admin/use-admin-auth';

const CSV_TEMPLATE_COLUMNS = [
  '问题',
  '能力维度',
  '评分分界线',
  '最好标准',
  '中等标准',
  '最差标准',
  '输出格式',
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
  const [jobDetail, setJobDetail] = useState<JobDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [jobName, setJobName] = useState('');
  const [jobDuties, setJobDuties] = useState('');
  const [jobRequirements, setJobRequirements] = useState('');
  const [jobNotes, setJobNotes] = useState('');
  const [jobFile, setJobFile] = useState<File | null>(null);
  const [creatingJob, setCreatingJob] = useState(false);

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
    if (!window.confirm('确认删除该岗位？关联面试记录也会删除。')) {
      return;
    }
    try {
      await adminApi.deleteJob(jobUid);
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

      {(detailLoading || jobDetail) && (
        <AdminModal title="岗位详情" onClose={() => setJobDetail(null)}>
          {detailLoading && <p className="admin-loading">加载详情中...</p>}
          {!detailLoading && jobDetail && (
            <article className="admin-detail-article">
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
            </article>
          )}
        </AdminModal>
      )}
    </AdminShell>
  );
};
