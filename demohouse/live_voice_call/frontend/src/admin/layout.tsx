import { ReactNode } from 'react';
import { useNavigate } from '@modern-js/runtime/router';

type TabKey = 'jobs' | 'interviews';

type AdminShellProps = {
  activeTab: TabKey;
  username: string;
  globalError?: string;
  toolbar: ReactNode;
  children: ReactNode;
  onLogout: () => Promise<void> | void;
};

export const AdminShell = ({
  activeTab,
  username,
  globalError,
  toolbar,
  children,
  onLogout,
}: AdminShellProps) => {
  const navigate = useNavigate();

  return (
    <main className="admin-page">
      <header className="admin-header">
        <div>
          <h1>AI 面试官管理后台</h1>
          <p>管理员：{username}</p>
        </div>
        <button type="button" className="admin-ghost-btn" onClick={onLogout}>
          退出登录
        </button>
      </header>

      {globalError && <p className="admin-error">{globalError}</p>}

      <section className="admin-toolbar">
        <div className="admin-tab-group">
          <button
            type="button"
            className={`admin-tab ${activeTab === 'jobs' ? 'is-active' : ''}`}
            onClick={() => navigate('/admin/jobs')}
          >
            岗位管理
          </button>
          <button
            type="button"
            className={`admin-tab ${activeTab === 'interviews' ? 'is-active' : ''}`}
            onClick={() => navigate('/admin/interviews')}
          >
            面试管理
          </button>
        </div>

        {toolbar}
      </section>

      {children}
    </main>
  );
};

export const AdminLoadingPage = () => (
  <main className="admin-page">
    <p className="admin-loading">正在验证管理员登录状态...</p>
  </main>
);

type AdminModalProps = {
  title: string;
  onClose: () => void;
  children: ReactNode;
};

export const AdminModal = ({ title, onClose, children }: AdminModalProps) => (
  <div className="admin-modal-mask" role="presentation">
    <section className="admin-modal" role="dialog" aria-modal="true">
      <h3>{title}</h3>
      {children}
      <button type="button" className="admin-modal-close" onClick={onClose} aria-label="关闭弹窗">
        关闭
      </button>
    </section>
  </div>
);
