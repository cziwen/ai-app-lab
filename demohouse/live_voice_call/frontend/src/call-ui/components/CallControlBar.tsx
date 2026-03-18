import type { CallControlAction, CallControlBarProps } from '@/call-ui/types';

type ControlButtonProps = {
  label: string;
  active?: boolean;
  danger?: boolean;
  action: CallControlAction;
  onAction: (action: CallControlAction) => void;
};

const ControlButton = ({
  label,
  active = false,
  danger = false,
  action,
  onAction,
}: ControlButtonProps) => (
  <button
    type="button"
    className={`control-btn ${active ? 'is-active' : ''} ${
      danger ? 'is-danger' : ''
    }`}
    onClick={() => onAction(action)}
  >
    {label}
  </button>
);

export const CallControlBar = ({
  isInCall,
  debugAllowed,
  onAction,
}: CallControlBarProps) => {
  return (
    <nav className="control-bar" aria-label="通话控制">
      {debugAllowed && (
        <ControlButton label="更多" action="toggleDebug" onAction={onAction} />
      )}
      <ControlButton
        label={isInCall ? '挂断' : '结束'}
        action="hangUp"
        danger
        onAction={onAction}
      />
    </nav>
  );
};
