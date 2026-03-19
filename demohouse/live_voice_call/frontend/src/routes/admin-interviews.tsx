import { type FormEvent, useEffect, useMemo, useState } from 'react';
import { API_URL } from '@/config/endpoints';
import {
  adminApi,
  type CheckInKey,
  type InterviewDetail,
  type InterviewListItem,
  type JobListItem,
} from '@/admin/api';
import { AdminLoadingPage, AdminModal, AdminShell } from '@/admin/layout';
import { useAdminAuth } from '@/admin/use-admin-auth';

const normalizeDurationMinutes = (value: string): number => {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || Number.isNaN(parsed)) {
    return 5;
  }
  return Math.max(5, parsed);
};

const CHECKIN_OPTIONS: Array<{ key: CheckInKey; label: string }> = [
  { key: 'speaker', label: '扬声器' },
  { key: 'mic', label: '麦克风' },
  { key: 'camera', label: '摄像头' },
  { key: 'screen', label: '屏幕共享' },
];

const CHECKIN_LABEL: Record<CheckInKey, string> = {
  speaker: '扬声器',
  mic: '麦克风',
  camera: '摄像头',
  screen: '屏幕共享',
};

export const AdminInterviewsPage = () => {
  const { loadingAuth, username, globalError, setGlobalError, handleLogout } = useAdminAuth();
  const [interviewSearch, setInterviewSearch] = useState('');
  const [interviews, setInterviews] = useState<InterviewListItem[]>([]);
  const [loadingInterviews, setLoadingInterviews] = useState(false);

  const [jobs, setJobs] = useState<JobListItem[]>([]);
  const [showCreateInterview, setShowCreateInterview] = useState(false);
  const [interviewDetail, setInterviewDetail] = useState<InterviewDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [candidateName, setCandidateName] = useState('');
  const [selectedJobUid, setSelectedJobUid] = useState('');
  const [durationMinutesInput, setDurationMinutesInput] = useState('10');
  const [interviewNotes, setInterviewNotes] = useState('');
  const [requiredCheckins, setRequiredCheckins] = useState<CheckInKey[]>([
    'speaker',
    'mic',
  ]);
  const [creatingInterview, setCreatingInterview] = useState(false);

  const loadJobs = async () => {
    try {
      const data = await adminApi.listJobs('');
      const items = data.items || [];
      setJobs(items);
      if (!selectedJobUid && items.length) {
        setSelectedJobUid(items[0].job_uid);
      }
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : '加载岗位失败');
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
    if (loadingAuth) {
      return;
    }
    Promise.all([loadJobs(), loadInterviews('')]);
  }, [loadingAuth]);

  const selectedJobQuestionCount = useMemo(() => {
    const matched = jobs.find(item => item.job_uid === selectedJobUid);
    return matched ? matched.question_count : 0;
  }, [jobs, selectedJobUid]);

  const estimatedQuestionCount = useMemo(() => {
    if (!selectedJobQuestionCount) {
      return 0;
    }
    const durationMinutes = normalizeDurationMinutes(durationMinutesInput);
    const planned = Math.max(1, Math.floor((durationMinutes - 5) / 5));
    return Math.min(selectedJobQuestionCount, planned);
  }, [durationMinutesInput, selectedJobQuestionCount]);

  const openInterviewDetail = async (token: string) => {
    setDetailLoading(true);
    setInterviewDetail(null);
    try {
      const data = await adminApi.getInterview(token);
      setInterviewDetail(data.interview);
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : '加载面试详情失败');
    } finally {
      setDetailLoading(false);
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

  const handleCreateInterview = async (event: FormEvent) => {
    event.preventDefault();
    setGlobalError('');
    setCreatingInterview(true);
    const durationMinutes = normalizeDurationMinutes(durationMinutesInput);
    try {
      await adminApi.createInterview({
        candidate_name: candidateName.trim(),
        job_uid: selectedJobUid,
        duration_minutes: durationMinutes,
        notes: interviewNotes.trim(),
        required_checkins: requiredCheckins,
      });
      setShowCreateInterview(false);
      setCandidateName('');
      setInterviewNotes('');
      setDurationMinutesInput('10');
      setRequiredCheckins(['speaker', 'mic']);
      await loadInterviews('');
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : '创建面试失败');
    } finally {
      setCreatingInterview(false);
    }
  };

  if (loadingAuth) {
    return <AdminLoadingPage />;
  }

  return (
    <AdminShell
      activeTab="interviews"
      username={username}
      globalError={globalError}
      onLogout={handleLogout}
      toolbar={
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
      }
    >
      <section className="admin-list-card">
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
      </section>

      {showCreateInterview && (
        <AdminModal title="创建面试" onClose={() => setShowCreateInterview(false)}>
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
              value={durationMinutesInput}
              onChange={event => setDurationMinutesInput(event.target.value)}
              onBlur={() => setDurationMinutesInput(String(normalizeDurationMinutes(durationMinutesInput)))}
              required
            />

            <p className="admin-hint">预计提问数：{estimatedQuestionCount}（包含 5 分钟 intro 预留）</p>

            <label>必检项配置</label>
            <div className="admin-checkin-grid">
              {CHECKIN_OPTIONS.map(item => (
                <label className="admin-checkin-item" key={item.key}>
                  <input
                    type="checkbox"
                    checked={requiredCheckins.includes(item.key)}
                    onChange={event => {
                      setRequiredCheckins(prev => {
                        if (event.target.checked) {
                          const next = new Set(prev);
                          next.add(item.key);
                          return CHECKIN_OPTIONS.map(option => option.key).filter(key =>
                            next.has(key),
                          );
                        }
                        return prev.filter(key => key !== item.key);
                      });
                    }}
                  />
                  <span>{item.label}</span>
                </label>
              ))}
            </div>
            <p className="admin-hint">
              默认勾选扬声器和麦克风。未勾选项不会出现在候选人 check-in 流程中。
            </p>

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
        </AdminModal>
      )}

      {(detailLoading || interviewDetail) && (
        <AdminModal title="面试详情" onClose={() => setInterviewDetail(null)}>
          {detailLoading && <p className="admin-loading">加载详情中...</p>}
          {!detailLoading && interviewDetail && (
            <article className="admin-detail-article">
              <h2 className="admin-detail-main-title">{interviewDetail.candidate_name}</h2>
              <p className="admin-detail-subtitle">
                token: {interviewDetail.token} | 状态: {interviewDetail.status}
              </p>

              <section className="admin-detail-grid">
                <p>岗位：{interviewDetail.job.name}</p>
                <p>岗位 UID：{interviewDetail.job.job_uid}</p>
                <p>时长：{interviewDetail.duration_minutes} 分钟</p>
                <p>题目数：{interviewDetail.question_count}</p>
                <p>创建时间：{interviewDetail.created_at}</p>
                <p>完成时间：{interviewDetail.completed_at || '未完成'}</p>
              </section>

              <section>
                <h3 className="admin-detail-title">面试链接</h3>
                <a href={interviewDetail.interview_link} target="_blank" rel="noreferrer">
                  {interviewDetail.interview_link}
                </a>
              </section>

              <section>
                <h3 className="admin-detail-title">备注</h3>
                <p>{interviewDetail.notes || '无'}</p>
              </section>

              <section>
                <h3 className="admin-detail-title">必检项</h3>
                <p>
                  {interviewDetail.required_checkins?.length
                    ? interviewDetail.required_checkins
                        .map(item => CHECKIN_LABEL[item] || item)
                        .join(' / ')
                    : '无（本场无需设备检查）'}
                </p>
              </section>

              <section>
                <h3 className="admin-detail-title">抽中问题</h3>
                {!!interviewDetail.selected_questions?.length && (
                  <ol className="admin-qa-list">
                    {interviewDetail.selected_questions.map(item => (
                      <li key={`${item.sort_order}-${item.question}`}>
                        {item.question}
                      </li>
                    ))}
                  </ol>
                )}
                {!interviewDetail.selected_questions?.length && <p>暂无抽题数据</p>}
              </section>

              {interviewDetail.completed ? (
                <>
                  <section>
                    <h3 className="admin-detail-title">对话记录</h3>
                    <ul className="admin-turn-list">
                      {(interviewDetail.turns || []).map((turn, index) => (
                        <li key={`${turn.created_at}-${index}`}>
                          <strong>{turn.role === 'candidate' ? '候选人' : '面试官'}：</strong>
                          {turn.content}
                        </li>
                      ))}
                    </ul>
                  </section>

                  <section>
                    <h3 className="admin-detail-title">音频</h3>
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
                  </section>
                </>
              ) : (
                <p className="admin-pending-msg">
                  {interviewDetail.completion_message || '用户还没有完成面试。'}
                </p>
              )}
            </article>
          )}
        </AdminModal>
      )}
    </AdminShell>
  );
};
