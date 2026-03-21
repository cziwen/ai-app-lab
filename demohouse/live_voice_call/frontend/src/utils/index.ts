// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// Licensed under the 【火山方舟】原型应用软件自用许可协议
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at 
//     https://www.volcengine.com/docs/82379/1433703
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License. 

import type { IWebSocketResponse, WebRequest } from '@/types';
import { CONST } from '@/constant';

/**
 * 打包成二进制数据(Blob)
 * @param req
 */
export function pack(req: WebRequest): Blob {
  if (req.payload) {
    const header = generateWSHeader(CONST.CLIENT_FULL_REQUEST);
    const json = JSON.stringify(req);
    const encoded = new TextEncoder().encode(json);
    const byteLength = encoded.length;
    header.setUint32(4, byteLength, false);
    return new Blob([header, json]);
  }
  const header = generateWSHeader(CONST.CLIENT_AUDIO_ONLY_REQUEST);
  const data = req.data || new Blob();
  header.setUint32(4, data.size, false);

  return new Blob([header, data]);
}

/**
 * 生成 WebSocket 消息头
 * @param msgType 消息类型
 * @description 将消息类型设置为指定的 msgType
 */
export const generateWSHeader = (msgType = CONST.CLIENT_AUDIO_ONLY_REQUEST) => {
  const buffer = new ArrayBuffer(8);
  const dataView = new DataView(buffer);
  dataView.setUint8(
    0,
    (CONST.PROTOCOL_VERSION << 4) | CONST.DEFAULT_HEADER_SIZE,
  );
  dataView.setUint8(1, msgType << 4 || CONST.NO_SEQUENCE);
  dataView.setUint8(2, (CONST.JSON << 4) | CONST.NO_COMPRESSION);
  dataView.setUint8(3, 0x00);

  return dataView;
};

/**
 * 解析 WebSocket 响应
 * @param resp 返回的 ArrayBuffer 对象
 */
export const decodeWebSocketResponse = (
  resp: ArrayBuffer,
): IWebSocketResponse => {
  const view = new DataView(resp);
  const header_size = view.getUint8(0) & 0x0f; // 0~3 bits
  const messageType = getHighNibble(view.getUint8(1));
  // const messageType = view.getUint8(1) & 0x0f; // 4~7 bits
  const payload = resp.slice(header_size * CONST.HEADER_BITS);
  const payloadSize = new DataView(payload).getUint32(0);
  const payloadBody = payload.slice(CONST.PAYLOAD_LENGTH_BYTES);
  if (messageType === CONST.SERVER_AUDIO_ONLY_RESPONSE) {
    return {
      messageType: CONST.SERVER_AUDIO_ONLY_RESPONSE,
      payload: payload.slice(
        CONST.PAYLOAD_LENGTH_BYTES,
        CONST.PAYLOAD_LENGTH_BYTES + payloadSize,
      ),
    };
  }
  return {
    messageType: CONST.SERVER_FULL_RESPONSE,
    payload: JSON.parse(new TextDecoder().decode(payloadBody)),
  };
};

/**
 * 获取一个字节的高 4 位
 * @param byte
 */
const getHighNibble = (byte: number) => {
  return (byte >> 4) & 0x0f;
};
