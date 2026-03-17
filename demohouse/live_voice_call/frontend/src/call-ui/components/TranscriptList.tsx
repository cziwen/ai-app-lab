import type { TranscriptListProps } from '@/call-ui/types';

export const TranscriptList = ({
  items,
  emptyText = '通话后展示消息记录...',
}: TranscriptListProps) => {
  if (!items.length) {
    return <p className="text-panel-empty">{emptyText}</p>;
  }
  return (
    <ul className="message-list">
      {items.map(item => (
        <li
          className={`message-item ${item.role === 'bot' ? 'is-bot' : 'is-user'}`}
          key={item.id}
        >
          <span className="message-role">
            {item.role === 'bot' ? '面试官' : '候选人'}
          </span>
          <p>{item.content}</p>
        </li>
      ))}
    </ul>
  );
};
