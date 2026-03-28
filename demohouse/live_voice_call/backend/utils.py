# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# Licensed under the 【火山方舟】原型应用软件自用许可协议
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at 
#     https://www.volcengine.com/docs/82379/1433703
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License. 

import struct
from typing import Any, Dict, Optional

from arkitect.utils.binary_protocol import (
    AUDIO_ONLY_SERVER,
    FULL_SERVER_RESPONSE,
    NO_COMPRESSION,
    NO_SEQUENCE,
    NO_SERIALIZATION,
    generate_header,
    parse_request,
)
from event import *


def _convert_json_request_to_web_event(req: Dict[str, Any]) -> WebEvent:
    raw_event = req.get("event")
    if not isinstance(raw_event, str) or not raw_event:
        raise ValueError("invalid event in json request")

    payload_obj: Optional[WebPayload] = None
    raw_payload = req.get("payload")
    if isinstance(raw_payload, dict):
        if raw_event == BOT_UPDATE_CONFIG:
            payload_obj = BotUpdateConfigPayload(**raw_payload)
        else:
            # Client control events may carry free-form payload for logging only.
            payload_obj = None

    return WebEvent(event=raw_event, payload=payload_obj)


def json_payload_to_binary_response(event: WebEvent) -> bytes:
    """
    Convert a JSON-formatted WebEvent event into a binary response.

    Args:
        event (WebEvent): The WebEvent event to be converted.

    Returns:
        bytes: The generated binary response.
    """
    # Convert the event object to a JSON string and encode it as bytes
    payload_bytes = str.encode(
        event.model_dump_json(exclude_none=True, exclude_unset=True)
    )
    # Generate the response header
    rsp_bytes = bytearray(
        generate_header(
            message_type=FULL_SERVER_RESPONSE,
            message_type_specific_flags=NO_SEQUENCE,
            compression_type=NO_COMPRESSION,
        )
    )
    # Pack the payload length as a 4-byte big-endian integer and add it to the response
    rsp_bytes.extend(struct.pack(">I", len(payload_bytes)))
    # Add the payload to the response
    rsp_bytes.extend(payload_bytes)
    return rsp_bytes


def audio_to_binary_response(audio_data: bytes) -> bytes:
    """
    Convert audio data into a binary response.

    Args:
        audio_data (bytes): The audio data to be converted.

    Returns:
        bytes: The generated binary response.
    """
    # Generate the audio response header
    rsp_bytes = bytearray(
        generate_header(
            message_type=AUDIO_ONLY_SERVER,
            message_type_specific_flags=NO_SEQUENCE,
            compression_type=NO_COMPRESSION,
            serial_method=NO_SERIALIZATION,
        )
    )
    # Pack the audio data length as a 4-byte big-endian integer and add it to the response
    rsp_bytes.extend(struct.pack(">I", len(audio_data)))  # payload size(4 bytes)
    # Add the audio data to the response
    rsp_bytes.extend(audio_data)
    return rsp_bytes


# Downlink message conversion
def convert_web_event_to_binary(event: WebEvent) -> bytes:
    """
    Convert a WebEvent event into a binary response.

    Args:
        event (WebEvent): The WebEvent event to be converted.

    Returns:
        bytes: The generated binary response.
    """
    # If the event contains data, convert it to an audio binary response
    if event.data:
        return audio_to_binary_response(event.data)
    # Otherwise, convert it to a JSON binary response
    else:
        return json_payload_to_binary_response(event)


# Uplink message conversion
def convert_binary_to_web_event_to_binary(data: bytes) -> WebEvent:
    """
    Parse binary data into a WebEvent event.

    Args:
        data (bytes): The binary data to be parsed.

    Returns:
        WebEvent: The parsed WebEvent event.
    """
    # Parse the request
    req = parse_request(data)
    # If the parsing result is a dictionary, convert it to a WebEvent object
    if isinstance(req, dict):
        return _convert_json_request_to_web_event(req)
    # Otherwise, create a new WebEvent object containing the audio data
    else:
        return WebEvent(event=USER_AUDIO, data=req)
