import { FormEvent, useEffect, useMemo, useState } from 'react';
import { useNavigate } from '@modern-js/runtime/router';
import { API_URL } from '@/config/endpoints';
import {
  adminApi,
  type InterviewDetail,
  type InterviewListItem,
  type JobDetail,
  type JobListItem,
} from '@/admin/api';

type TabType = 'jobs' | 'interviews';

export const AdminPage = () => {
  const navigate = useNavigate();
  const [loadingAuth, setLoadingAuth] = useState(true);
  const [username, setUsername] = useState('');

  const [tab, setTab] = useState<TabType>('jobs');
  const [jobSearch, setJobSearch] = useState('');
  const [interviewSearch, setInterviewSearch] = useState('');

  const [jobs, setJobs] = useState<JobListItem[]>([]);
  const [interviews, setInterviews] = useState<InterviewListItem[]>([]);
  const [loadingJobs, setLoadingJobs] = useState(false);
  const [loadingInterviews, setLoadingInterviews] = useState(false);

  const [jobDetail, setJobDetail] = useState<JobDetail | null>(null);
  const [interviewDetail, setInterviewDetail] = useState<InterviewDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [globalError, setGlobalError] = useState('');

  const [showCreateJob, setShowCreateJob] = useState(false);
  const [showCreateInterview, setShowCreateInterview] = useState(false);

  const [jobName, setJobName] = useState('');
  const [jobDuties, setJobDuties] = useState('');
  const [jobRequirements, setJobRequirements] = useState('');
  const [jobNotes, setJobNotes] = useState('');
  const [jobFile, setJobFile] = useState<File | null>(null);
  const [creatingJob, setCreatingJob] = useState(false);

  const [candidateName, setCandidateName] = useState('');
  const [selectedJobUid, setSelectedJobUid] = useState('');
  const [durationMinutes, setDurationMinutes] = useState(30);
  const [interviewNotes, setInterviewNotes] = useState('');
  const [creatingInterview, setCreatingInterview] = useState(false);

  const selectedJobQuestionCount = useMemo(() => {
    const matched = jobs.find(item => item.job_uid === selectedJobUid);
    return matched ? matched.question_count : 0;
  }, [jobs, selectedJobUid]);

  const estimatedQuestionCount = useMemo(() => {
    if (!selectedJobQuestionCount) {
      return 0;
    }
    const planned = Math.max(1, Math.floor((durationMinutes - 5) / 5));
    return Math.min(selectedJobQuestionCount, planned);
  }, [durationMinutes, selectedJobQuestionCount]);

  const loadJobs = async (query = jobSearch) => {
    setLoadingJobs(true);
    try {
      const data = await adminApi.listJobs(query);
      setJobs(data.items || []);
      if (!selectedJobUid && data.items?.length) {
        setSelectedJobUid(data.items[0].job_uid);
      }
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : '加载岗位失败');
    } finally {
      setLoadingJobs(false);
    }
  };

  const loadInterviews = async (query = interviewSearch) => {
    setLoadingInterviews(true);
    try {
      const data = await adminApi.listInterviews(query);
      setInterviews(data.items || []);
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : '加载面试失败');
    } finally {
      setLoadingInterviews(false);
    }
  };

  useEffect(() => {
    const boot = async () => {
      try {
        const me = await adminApi.me();
        setUsername(me.admin.username);
      } catch (_error) {
        navigate('/admin/login');
        return;
      }
      await Promise.all([loadJobs(''), loadInterviews('')]);
      setLoadingAuth(false);
    };
    boot();
  }, []);

  const handleLogout = async () => {
    await adminApi.logout();
    navigate('/admin/login');
  };

  const handleCreateJob = async (event: FormEvent) => {
    event.preventDefault();
    setGlobalError('');
    if (!jobFile) {
      setGlobalError('请上传题库 CSV');
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

  const handleCreateInterview = async (event: FormEvent) => {
    event.preventDefault();
    setGlobalError('');
    setCreatingInterview(true);
    try {
      await adminApi.createInterview({
        candidate_name: candidateName.trim(),
        job_uid: selectedJobUid,
        duration_minutes: durationMinutes,
        notes: interviewNotes.trim(),
      });
      setShowCreateInterview(false);
      setCandidateName('');
      setInterviewNotes('');
      setDurationMinutes(30);
      await loadInterviews('');
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : '创建面试失败');
    } finally {
      setCreatingInterview(false);
    }
  };

  const openJobDetail = async (jobUid: string) => {
    setDetailLoading(true);
    setInterviewDetail(null);
    try {
      const data = await adminApi.getJob(jobUid);
      setJobDetail(data.job);
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : '加载岗位详情失败');
    } finally {
      setDetailLoading(false);
    }
  };

  const openInterviewDetail = async (token: string) => {
    setDetailLoading(true);
    setJobDetail(null);
    try {
      const data = await adminApi.getInterview(token);
      setInterviewDetail(data.interview);
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : '加载面试详情失败');
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
      await Promise.all([loadJobs(), loadInterviews()]);
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : '删除岗位失败');
    }
  };

  const handleDeleteInterview = async (token: string) => {
    if (!window.confirm('确认删除该面试记录？')) {
      return;
    }
    try {
      await adminApi.deleteInterview(token);
      setInterviewDetail(null);
      await loadInterviews();
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : '删除面试失败');
    }
  };

  if (loadingAuth) {
    return (
      <main className="admin-page">
        <p className="admin-loading">正在验证管理员登录状态...</p>
      </main>
    );
  }

  return (
    <main className="admin-page">
      <header className="admin-header">
        <div>
          <h1>AI 面试官管理后台</h1>
          <p>管理员：{username}</p>
        </div>
        <button type="button" className="admin-ghost-btn" onClick={handleLogout}>
          退出登录
        </button>
      </header>

      {globalError && <p className="admin-error">{globalError}</p>}

      <section className="admin-toolbar">
        <div className="admin-tab-group">
          <button
            type="button"
            className={`admin-tab ${tab === 'jobs' ? 'is-active' : ''}`}
            onClick={() => setTab('jobs')}
          >
            岗位管理
          </button>
          <button
            type="button"
            className={`admin-tab ${tab === 'interviews' ? 'is-active' : ''}`}
            onClick={() => setTab('interviews')}
          >
            面试管理
          </button>
        </div>

        {tab === 'jobs' ? (
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
        ) : (
          <div className="admin-action-row">
            <input
              value={interviewSearch}
              onChange={event => setInterviewSearch(event.target.value)}
              placeholder="搜索候选人/岗位/token"
            />
            <button type="button" onClick={() => loadInterviews(interviewSearch)}>
              搜索
            </button>
            <button
              type="button"
              className="admin-primary-btn"
              onClick={() => setShowCreateInterview(true)}
            >
              创建面试
            </button>
          </div>
        )}
      </section>

      <section className="admin-content-grid">
        <div className="admin-list-card">
          {tab === 'jobs' ? (
            <>
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
            </>
          ) : (
            <>
              <h2>面试列表</h2>
              {loadingInterviews && <p className="admin-loading">加载中...</p>}
              <ul className="admin-list">
                {interviews.map(item => (
                  <li key={item.token}>
                    <div>
                      <strong>{item.candidate_name}</strong>
                      <p>
                        {item.job.name} | token: {item.token} | 状态: {item.status}
                      </p>
                    </div>
                    <div className="admin-list-actions">
                      <button type="button" onClick={() => openInterviewDetail(item.token)}>
                        查看详情
                      </button>
                      <button type="button" onClick={() => handleDeleteInterview(item.token)}>
                        删除
                      </button>
                    </div>
                  </li>
                ))}
                {!interviews.length && !loadingInterviews && <li>暂无面试</li>}
              </ul>
            </>
          )}
        </div>

        <div className="admin-detail-card">
          <h2>详细信息</h2>
          {detailLoading && <p className="admin-loading">加载详情中...</p>}

          {!detailLoading && !jobDetail && !interviewDetail && (
            <p>点击列表中的“查看详情”查看信息。</p>
          )}

          {!detailLoading && jobDetail && (
            <div className="admin-detail-block">
              <h3>{jobDetail.name}</h3>
              <p>UID: {jobDetail.job_uid}</p>
              <p>
                <strong>职责：</strong>
                {jobDetail.duties}
              </p>
              <p>
                <strong>要求：</strong>
                {jobDetail.requirements}
              </p>
              <p>
                <strong>补充：</strong>
                {jobDetail.notes || '无'}
              </p>
              <h4>题库</h4>
              <ol className="admin-qa-list">
                {jobDetail.questions.map(question => (
                  <li key={question.id}>
                    <p>
                      <strong>题目：</strong>
                      {question.question}
                    </p>
                    <p>
                      <strong>参考答案：</strong>
                      {question.reference_answer}
                    </p>
                  </li>
                ))}
              </ol>
            </div>
          )}

          {!detailLoading && interviewDetail && (
            <div className="admin-detail-block">
              <h3>{interviewDetail.candidate_name}</h3>
              <p>
                岗位：{interviewDetail.job.name}（{interviewDetail.job.job_uid}）
              </p>
              <p>
                时长：{interviewDetail.duration_minutes} 分钟 | 题目数：
                {interviewDetail.question_count}
              </p>
              <p>状态：{interviewDetail.status}</p>
              <p>备注：{interviewDetail.notes || '无'}</p>
              <p>
                面试链接：
                <a href={interviewDetail.interview_link} target="_blank" rel="noreferrer">
                  {interviewDetail.interview_link}
                </a>
              </p>

              {interviewDetail.completed ? (
                <>
                  <h4>对话记录</h4>
                  <ul className="admin-turn-list">
                    {(interviewDetail.turns || []).map((turn, index) => (
                      <li key={`${turn.created_at}-${index}`}>
                        <strong>{turn.role === 'candidate' ? '候选人' : '面试官'}：</strong>
                        {turn.content}
                      </li>
                    ))}
                  </ul>

                  <h4>音频</h4>
                  <div className="admin-audio-row">
                    <div>
                      <p>候选人音轨</p>
                      <audio controls src={`${API_URL}${interviewDetail.audio?.candidate_url || ''}`} />
                    </div>
                    <div>
                      <p>面试官音轨</p>
                      <audio
                        controls
                        src={`${API_URL}${interviewDetail.audio?.interviewer_url || ''}`}
                      />
                    </div>
                  </div>
                </>
              ) : (
                <p className="admin-pending-msg">用户还没有完成面试。</p>
              )}
            </div>
          )}
        </div>
      </section>

      {showCreateJob && (
        <div className="admin-modal-mask" role="presentation">
          <section className="admin-modal" role="dialog" aria-modal="true">
            <h3>创建岗位</h3>
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
          </section>
        </div>
      )}

      {showCreateInterview && (
        <div className="admin-modal-mask" role="presentation">
          <section className="admin-modal" role="dialog" aria-modal="true">
            <h3>创建面试</h3>
            <form onSubmit={handleCreateInterview}>
              <label htmlFor="candidate-name">候选人姓名</label>
              <input
                id="candidate-name"
                value={candidateName}
                onChange={event => setCandidateName(event.target.value)}
                required
              />

              <label htmlFor="interview-job">申请岗位</label>
              <select
                id="interview-job"
                value={selectedJobUid}
                onChange={event => setSelectedJobUid(event.target.value)}
                required
              >
                {jobs.map(job => (
                  <option value={job.job_uid} key={job.job_uid}>
                    {job.name} ({job.job_uid})
                  </option>
                ))}
              </select>

              <label htmlFor="interview-duration">面试时长（分钟）</label>
              <input
                id="interview-duration"
                type="number"
                min={5}
                max={180}
                value={durationMinutes}
                onChange={event => setDurationMinutes(Number(event.target.value) || 5)}
                required
              />

              <p className="admin-hint">预计提问数：{estimatedQuestionCount}（包含 5 分钟 intro 预留）</p>

              <label htmlFor="interview-notes">备注（可选）</label>
              <textarea
                id="interview-notes"
                value={interviewNotes}
                onChange={event => setInterviewNotes(event.target.value)}
              />

              <div className="admin-modal-actions">
                <button type="button" onClick={() => setShowCreateInterview(false)}>
                  取消
                </button>
                <button
                  type="submit"
                  disabled={creatingInterview || !candidateName.trim() || !selectedJobUid}
                >
                  {creatingInterview ? '创建中...' : '提交创建'}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </main>
  );
};
