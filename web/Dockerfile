# Web 的正式環境映像：vite build 出靜態檔，交給 nginx。
#
# 開發時只有 vite dev server（5173），沒有任何 production 的 serve 方式 ——
# 這份補的就是那個缺口。

FROM node:20-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# **同源部署**：VITE_HUB_URL 烘成空字串，api.ts 的 `${HUB}/api/...` 就變成相對路徑，
# 由 nginx 把 /api/ 轉給 hub。好處是瀏覽器只看到一個 origin，CORS 整個不用管，
# 對外也只開一個 port。改成跨網域部署時這裡要填 hub 的完整網址。
ARG VITE_HUB_URL=""
ENV VITE_HUB_URL=$VITE_HUB_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /src/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
