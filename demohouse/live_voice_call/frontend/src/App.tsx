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

import { BrowserRouter, Navigate, Route, Routes } from '@modern-js/runtime/router';
import { SessionAuthProvider } from '@/auth/context';
import {
  RequireActiveInterviewToken,
  RequireToken,
  RequireTokenAndCheckIn,
} from '@/auth/guards';
import { AdminInterviewsPage } from '@/routes/admin-interviews';
import { AdminJobsPage } from '@/routes/admin-jobs';
import { AdminLoginPage } from '@/routes/admin-login';
import { CheckInPage } from '@/routes/check-in';
import { HangupResultPage } from '@/routes/hangup-result';
import { InvalidLinkPage } from '@/routes/invalid-link';
import Demo from './routes/page';
import './index.css';
export default () => {
  return (
    <BrowserRouter>
      <SessionAuthProvider>
        <Routes>
          <Route
            path="/"
            element={
              <RequireTokenAndCheckIn>
                <Demo />
              </RequireTokenAndCheckIn>
            }
          />
          <Route
            path="/check-in"
            element={
              <RequireActiveInterviewToken>
                <CheckInPage />
              </RequireActiveInterviewToken>
            }
          />
          <Route
            path="/hangup-result"
            element={
              <RequireToken>
                <HangupResultPage />
              </RequireToken>
            }
          />
          <Route path="/invalid-link" element={<InvalidLinkPage />} />
          <Route path="/admin/login" element={<AdminLoginPage />} />
          <Route path="/admin" element={<Navigate replace to="/admin/jobs" />} />
          <Route path="/admin/jobs" element={<AdminJobsPage />} />
          <Route path="/admin/interviews" element={<AdminInterviewsPage />} />
        </Routes>
      </SessionAuthProvider>
    </BrowserRouter>
  );
};
