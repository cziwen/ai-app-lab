import type { CallControlAction, CallControlBarProps } from '@/call-ui/types';

type ControlButtonProps = {
  label: string;
  active?: boolean;
  danger?: boolean;
  disabled?: boolean;
  action: CallControlAction;
  onAction: (action: CallControlAction) => void;
};

const ControlButton = ({
  label,
  active = false,
  danger = false,
  disabled = false,
  action,
  onAction,
}: ControlButtonProps) => (
  <button
    type="button"
    className={`control-btn ${active ? 'is-active' : ''} ${
      danger ? 'is-danger' : ''
    }`}
    disabled={disabled}
    onClick={() => onAction(action)}
  >
    {label}
  </button>
);

export const CallControlBar = ({
  isInCall,
  showEndAnswerButton = false,
  endAnswerEnabled = false,
  debugAllowed,
  onAction,
}: CallControlBarProps) => {
  return (
    <nav className="control-bar" aria-label="通话控制">
      {debugAllowed && (
        <ControlButton label="更多" action="toggleDebug" onAction={onAction} />
      )}
      {showEndAnswerButton && (
        <ControlButton
          label="结束本题"
          action="endAnswer"
          disabled={!endAnswerEnabled}
          onAction={onAction}
        />
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
