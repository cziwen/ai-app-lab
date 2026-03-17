export type PermissionStatus = 'pending' | 'granted' | 'denied';

export type PermissionKey = 'mic' | 'camera' | 'screen' | 'speaker';

export interface PermissionState {
  mic: PermissionStatus;
  camera: PermissionStatus;
  screen: PermissionStatus;
  speaker: PermissionStatus;
}
