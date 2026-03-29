import { type FormEvent, useEffect, useState } from 'react';
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

const COMPLETED_REASON_LABEL: Record<string, string> = {
  normal_end: '正常结束',
  hangup: '主动挂断',
  disconnect: '断连结束',
  error: '异常结束',
};

const formatCompletedReason = (reason?: string | null): string => {
  if (!reason) {
    return '';
  }
  return COMPLETED_REASON_LABEL[reason] || reason;
};

const SCORECARD_STATUS_LABEL: Record<string, string> = {
  pending: '评分中',
  completed: '评分完成',
  failed: '评分失败',
};

const formatScorecardStatus = (status?: string): string => {
  if (!status) {
    return '评分中';
  }
  return SCORECARD_STATUS_LABEL[status] || status;
};

export const AdminInterviewsPage = () => {
  const { loadingAuth, username, globalError, setGlobalError, handleLogout } = useAdminAuth();
  const [interviewSearch, setInterviewSearch] = useState('');
  const [interviews, setInterviews] = useState<InterviewListItem[]>([]);
  const [loadingInterviews, setLoadingInterviews] = useState(false);
  const [activeInterviews, setActiveInterviews] = useState<number | null>(null);
  const [maxActiveInterviews, setMaxActiveInterviews] = useState<number | null>(null);

  const [jobs, setJobs] = useState<JobListItem[]>([]);
  const [showCreateInterview, setShowCreateInterview] = useState(false);
  const [interviewDetail, setInterviewDetail] = useState<InterviewDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [candidateName, setCandidateName] = useState('');
  const [selectedJobUid, setSelectedJobUid] = useState('');
  const [selectedJobQuestions, setSelectedJobQuestions] = useState<Array<{ id: number; question: string }>>(
    [],
  );
  const [questionFollowupInputs, setQuestionFollowupInputs] = useState<Record<number, string>>({});
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

  const loadInterviewMetrics = async () => {
    try {
      const data = await adminApi.getInterviewMetrics();
      setActiveInterviews(data.active_interviews);
      setMaxActiveInterviews(data.max_active_interviews);
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : '加载实时面试人数失败');
    }
  };

  useEffect(() => {
    if (loadingAuth) {
      return;
    }
    Promise.all([loadJobs(), loadInterviews(''), loadInterviewMetrics()]);
  }, [loadingAuth]);

  useEffect(() => {
    if (loadingAuth) {
      return;
    }
    const timer = window.setInterval(() => {
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') {
        return;
      }
      loadInterviewMetrics();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [loadingAuth]);

  useEffect(() => {
    const loadSelectedJobQuestions = async () => {
      if (!selectedJobUid) {
        setSelectedJobQuestions([]);
        setQuestionFollowupInputs({});
        return;
      }
      try {
        const data = await adminApi.getJob(selectedJobUid);
        const questions = (data.job.questions || []).map(item => ({
          id: item.id,
          question: item.question,
        }));
        setSelectedJobQuestions(questions);
        setQuestionFollowupInputs(
          questions.reduce<Record<number, string>>((acc, question) => {
            acc[question.id] = '0';
            return acc;
          }, {}),
        );
      } catch (e) {
        setGlobalError(e instanceof Error ? e.message : '加载岗位题目失败');
      }
    };
    loadSelectedJobQuestions();
  }, [selectedJobUid]);

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
    try {
      const question_followups = selectedJobQuestions.map(question => {
        const raw = questionFollowupInputs[question.id] ?? '0';
        const parsed = Number.parseInt(raw, 10);
        if (!Number.isFinite(parsed) || Number.isNaN(parsed) || parsed < 0 || parsed > 3) {
          throw new Error(`题目「${question.question}」的追问次数必须是 0-3 的整数`);
        }
        return {
          question_id: question.id,
          max_followups: parsed,
        };
      });
      await adminApi.createInterview({
        candidate_name: candidateName.trim(),
        job_uid: selectedJobUid,
        question_followups,
        notes: interviewNotes.trim(),
        required_checkins: requiredCheckins,
      });
      setShowCreateInterview(false);
      setCandidateName('');
      setInterviewNotes('');
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
        <p>
          正在面试：
          {activeInterviews === null || maxActiveInterviews === null
            ? '加载中...'
            : `${activeInterviews} / ${maxActiveInterviews}`}
        </p>
        {loadingInterviews && <p className="admin-loading">加载中...</p>}
        <ul className="admin-list">
          {interviews.map(item => (
            <li key={item.token}>
              <div>
                <strong>{item.candidate_name}</strong>
                <p>
                  {item.job.name} | token: {item.token} | 状态: {item.status}
                </p>
                {item.status === 'completed' && item.completed_reason && (
                  <p>完成原因：{formatCompletedReason(item.completed_reason)}</p>
                )}
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

            <label>逐题追问次数（0-3）</label>
            <div className="admin-qa-list">
              {selectedJobQuestions.map(item => (
                <div key={item.id} className="admin-followup-row">
                  <p>{item.question}</p>
                  <input
                    type="number"
                    min={0}
                    max={3}
                    value={questionFollowupInputs[item.id] ?? '0'}
                    onChange={event =>
                      setQuestionFollowupInputs(prev => ({
                        ...prev,
                        [item.id]: event.target.value,
                      }))
                    }
                    required
                  />
                </div>
              ))}
              {!selectedJobQuestions.length && <p>当前岗位题库为空，无法创建面试。</p>}
            </div>

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
                disabled={
                  creatingInterview ||
                  !candidateName.trim() ||
                  !selectedJobUid ||
                  selectedJobQuestions.length === 0
                }
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
                <p>题目数：{interviewDetail.question_count}</p>
                <p>创建时间：{interviewDetail.created_at}</p>
                <p>完成时间：{interviewDetail.completed_at || '未完成'}</p>
                {interviewDetail.status === 'completed' &&
                  interviewDetail.completed_reason && (
                    <p>
                      完成原因：
                      {formatCompletedReason(interviewDetail.completed_reason)}
                    </p>
                  )}
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
                        {item.question}（追问上限: {item.max_followups}）
                      </li>
                    ))}
                  </ol>
                )}
                {!interviewDetail.selected_questions?.length && <p>暂无抽题数据</p>}
              </section>

              <section>
                <h3 className="admin-detail-title">AI 评分</h3>
                <p>状态：{formatScorecardStatus(interviewDetail.scorecard?.status)}</p>
                {interviewDetail.scorecard?.status === 'completed' && (
                  <p>
                    总分：
                    {(
                      (interviewDetail.scorecard?.question_scores || []).reduce(
                        (sum, item) => sum + (Number(item.numeric_score) || 0),
                        0,
                      )
                    ).toFixed(2)}{' '}
                    / {(((interviewDetail.scorecard?.question_scores || []).length || 0) * 5).toFixed(2)}
                  </p>
                )}
                {interviewDetail.scorecard?.status === 'failed' && (
                  <p>失败原因：{interviewDetail.scorecard?.error_message || '评分服务异常'}</p>
                )}
                {!!interviewDetail.scorecard?.question_scores?.length && (
                  <ol className="admin-qa-list">
                    {interviewDetail.scorecard.question_scores.map(item => (
                      <li key={`${item.question_id}-${item.sort_order}`}>
                        <p>
                          {item.sort_order}. {item.question}
                        </p>
                        <p>分数：{item.numeric_score.toFixed(2)} / 5.00</p>
                        {item.ability_dimension && <p>能力维度：{item.ability_dimension}</p>}
                        {item.output_format && <p>输出格式：{item.output_format}</p>}
                        <p>评语：{item.comment}</p>
                      </li>
                    ))}
                  </ol>
                )}
                {!interviewDetail.scorecard?.question_scores?.length && (
                  <p>暂无逐题评分数据。</p>
                )}
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
