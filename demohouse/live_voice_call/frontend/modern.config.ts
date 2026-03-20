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

import { appTools, defineConfig } from '@modern-js/app-tools';
import { tailwindcssPlugin } from '@modern-js/plugin-tailwindcss';

const adminApiProxyTarget =
  process.env.ADMIN_API_PROXY_TARGET?.trim() || 'http://127.0.0.1:8890';
// https://modernjs.dev/en/configure/app/usage
export default defineConfig({
  runtime: {
    router: false,
  },
  tools: {
    devServer: {
      proxy: {
        '/api/frontend-logs': {
          target: 'http://127.0.0.1:8889',
          changeOrigin: true,
        },
        '/api': {
          target: adminApiProxyTarget,
          changeOrigin: true,
        },
        '/ws': {
          target: 'ws://127.0.0.1:8888',
          ws: true,
          changeOrigin: true,
        },
      },
    },
  },
  html: {
    disableHtmlFolder: true,
  },
  source: {
    mainEntryName: 'index',
  },
  output: {
    distPath: {
      html: '/',
    },
  },
  // server:{
  //   baseUrl: '/'
  // },
  plugins: [
    appTools({
      bundler: 'rspack', // Set to 'webpack' to enable webpack
    }),
    tailwindcssPlugin(),
  ],
});
