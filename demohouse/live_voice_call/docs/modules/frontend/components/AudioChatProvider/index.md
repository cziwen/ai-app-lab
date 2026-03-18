# frontend/src/components/AudioChatProvider/index.tsx

## 模块职责
- 负责 `components/AudioChatProvider/index` 对应的前端能力（页面/组件/hook/类型/工具）。

## 入口与调用方
- 通过路由、组件树或 Provider 链接入主流程。
- 路由主入口参考：`frontend/src/routes/*`。

## 对外接口（导出项）
L16: export const AudioChatProvider: FC<PropsWithChildren> = ({ children }) => {

## 依赖与配置
- 关键导入（节选）：
L12: import { type FC, type PropsWithChildren, useState } from 'react';
L13: import { AudioChatContext } from '@/components/AudioChatProvider/context';
L14: import type { IMessage } from '@/types';

## 日志与排障
- 优先观察浏览器控制台与前端日志上报接口（`/api/frontend-logs`）。
- 涉及实时通话时联动检查 WebSocket 连接、麦克风权限与录音状态。

## 常见故障与排查步骤
1. 页面空白或跳转异常：检查路由守卫与 token/session 参数。
2. 无语音输入：检查浏览器权限、音频设备选择与录音 hook 状态。
3. 无机器人回复：检查 WS URL、连接状态与后端事件回包。

## 相关测试/验证
- 本项目以前端手工联调为主，建议使用 check-in -> 通话 -> 挂断全链路回归。
