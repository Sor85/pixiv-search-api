from fastapi import FastAPI, HTTPException, Query
from pixivpy3 import AppPixivAPI
import asyncio
import os
from typing import List, Optional
import random
from fastapi.responses import RedirectResponse, StreamingResponse
import mimetypes
import json
from datetime import datetime, timedelta, timezone
import httpx

app = FastAPI()

# Pixiv API凭证 
PIXIV_REFRESH_TOKEN = os.environ.get("PIXIV_REFRESH_TOKEN")

if not PIXIV_REFRESH_TOKEN:
    raise RuntimeError("PIXIV_REFRESH_TOKEN 环境变量未设置。")

aapi = AppPixivAPI()

MAX_PAGES_TO_FETCH = 3 # 最多获取3页数据

# --- 12小时去重机制 ---
RECENTLY_SEEN_ILLUSTS_STORE = "recently_seen_illusts.json"
SEEN_ILLUSTS_EXPIRY_HOURS = 12
seen_illusts_lock = asyncio.Lock()

async def mark_illust_as_seen(illust_id: int):
    illust_id_str = str(illust_id)
    async with seen_illusts_lock:
        seen_data = {}
        try:
            with open(RECENTLY_SEEN_ILLUSTS_STORE, "r", encoding='utf-8') as f:
                content = f.read()
                if content:
                    seen_data = json.loads(content)
        except FileNotFoundError:
            pass
        except json.JSONDecodeError:
            print(f"警告: 文件 {RECENTLY_SEEN_ILLUSTS_STORE} 解析错误，将重新创建。")
            seen_data = {}

        seen_data[illust_id_str] = datetime.now(timezone.utc).isoformat()

        cutoff_cleanup = datetime.now(timezone.utc) - timedelta(hours=SEEN_ILLUSTS_EXPIRY_HOURS * 2)
        
        cleaned_data = {}
        for pid, ts_str in seen_data.items():
            try:
                ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if not ts_dt.tzinfo:
                     ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                if ts_dt >= cutoff_cleanup:
                    cleaned_data[pid] = ts_str
            except ValueError:
                print(f"警告: 清理时遇到无效的时间戳格式 '{ts_str}' for ID {pid}。跳过此条记录。")
        
        try:
            with open(RECENTLY_SEEN_ILLUSTS_STORE, "w", encoding='utf-8') as f:
                json.dump(cleaned_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"错误: 无法保存到 {RECENTLY_SEEN_ILLUSTS_STORE}: {e}")

async def is_illust_recently_seen(illust_id: int) -> bool:
    illust_id_str = str(illust_id)
    async with seen_illusts_lock:
        seen_data = {}
        try:
            with open(RECENTLY_SEEN_ILLUSTS_STORE, "r", encoding='utf-8') as f:
                content = f.read()
                if content:
                    seen_data = json.loads(content)
        except FileNotFoundError:
            return False
        except json.JSONDecodeError:
            print(f"警告: 文件 {RECENTLY_SEEN_ILLUSTS_STORE} 解析错误。暂时认为所有图片未见过。")
            return False 

    if illust_id_str not in seen_data:
        return False
    
    last_seen_ts_str = seen_data[illust_id_str]
    try:
        last_seen_dt = datetime.fromisoformat(last_seen_ts_str.replace("Z", "+00:00"))
        if not last_seen_dt.tzinfo:
            last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)
        
        expiry_threshold = datetime.now(timezone.utc) - timedelta(hours=SEEN_ILLUSTS_EXPIRY_HOURS)
        return last_seen_dt > expiry_threshold
    except ValueError:
        print(f"警告: 解析时间戳 {last_seen_ts_str} (ID: {illust_id_str}) 出错。暂时认为此图片未见过。")
        return False
# --- 12小时去重机制 ---

# Pixiv认证函数
async def authenticate_pixiv():
    try:
        aapi.auth(refresh_token=PIXIV_REFRESH_TOKEN)
    except Exception as e:
        print(f"Pixiv认证过程中出错: {e}")

@app.on_event("startup")
async def startup_event():
    await authenticate_pixiv()

@app.get("/")
async def read_root():
    return {"message": "Pixiv 搜索 API"}


@app.get("/pixiv/direct")
async def search_pixiv_illustrations(
    keyword: Optional[str] = None,
    r18: Optional[int] = Query(0, ge=0, le=1),
    min_bookmarks: Optional[int] = Query(None, ge=0),
    ai: Optional[int] = Query(0, ge=0, le=2),
    sort_order: Optional[int] = Query(0, ge=0, le=3),
    pid: Optional[str] = Query(None)
):
    await authenticate_pixiv()

    if not keyword and not (pid and pid.isdigit()):
        raise HTTPException(status_code=400, detail="必须提供 keyword 或有效的 pid")

    if pid and pid.isdigit():
        illust_id = int(pid)
        try:
            json_result = await asyncio.to_thread(aapi.illust_detail, illust_id)
            if not json_result or json_result.get('error'):
                error_message = json_result.get('error', {}).get('message', '未知错误')
                raise HTTPException(status_code=404, detail=f"获取作品(pid:{illust_id})失败: {error_message}")
            
            illust = json_result.illust
            if not illust:
                raise HTTPException(status_code=404, detail=f"未找到PID为 {illust_id} 的作品。")

            if r18 == 0 and illust.x_restrict != 0:
                raise HTTPException(status_code=403, detail=f"作品(pid:{illust_id})是R-18内容，但请求参数r18=0。")
            if r18 == 1 and illust.x_restrict == 0:
                raise HTTPException(status_code=403, detail=f"作品(pid:{illust_id})是全年龄内容，但请求参数r18=1。")

            if ai == 1 and illust.illust_ai_type == 2:
                raise HTTPException(status_code=403, detail=f"作品(pid:{illust_id})是AI生成内容，但请求参数ai=1。")
            if ai == 2 and illust.illust_ai_type != 2:
                raise HTTPException(status_code=403, detail=f"作品(pid:{illust_id})是非AI生成内容，但请求参数ai=2。")

            image_url_to_proxy = None
            if illust.meta_single_page.get('original_image_url'):
                image_url_to_proxy = illust.meta_single_page.get('original_image_url')
            elif illust.meta_pages:
                selected_page = random.choice(illust.meta_pages)
                if selected_page.image_urls.get('original'):
                    image_url_to_proxy = selected_page.image_urls.get('original')
                elif selected_page.image_urls.get('large'):
                    image_url_to_proxy = selected_page.image_urls.get('large')
                elif selected_page.image_urls.get('medium'):
                    image_url_to_proxy = selected_page.image_urls.get('medium')

            if image_url_to_proxy:
                async with httpx.AsyncClient() as client:
                    headers = {'Referer': 'https://www.pixiv.net/'}
                    try:
                        resp = await client.get(image_url_to_proxy, headers=headers, timeout=30.0)
                        resp.raise_for_status()

                        content_type = resp.headers.get('Content-Type', 'application/octet-stream')

                        return StreamingResponse(resp.iter_bytes(), media_type=content_type)
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code in [403, 404]:
                            raise HTTPException(status_code=e.response.status_code, detail=f"无法从Pixiv获取图片(pid:{illust_id})，链接可能已失效。")
                        else:
                            raise HTTPException(status_code=500, detail=f"代理请求Pixiv图片时发生网络错误: {e}")
                    except httpx.RequestError as e:
                        raise HTTPException(status_code=502, detail=f"代理请求Pixiv图片时网络连接失败: {e}")

            else:
                raise HTTPException(status_code=404, detail=f"无法找到PID为 {illust_id} 的作品的图片URL。")

        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=500, detail=f"处理PID {illust_id} 时发生服务器内部错误: {str(e)}")

    if keyword:
        tags = keyword.split(',')
        search_query = " ".join(tags)

        if r18 == 1:
            search_query += " R-18"

        try:
            all_illusts = []
            
            sort_strategies = []
            if sort_order == 0:
                pages_for_date_desc = (MAX_PAGES_TO_FETCH + 1) // 2
                pages_for_date_asc = MAX_PAGES_TO_FETCH // 2
                if pages_for_date_desc > 0:
                    sort_strategies.append({'sort': 'date_desc', 'pages': pages_for_date_desc})
                if pages_for_date_asc > 0:
                    sort_strategies.append({'sort': 'date_asc', 'pages': pages_for_date_asc})
            elif sort_order == 1:
                sort_strategies.append({'sort': 'date_desc', 'pages': MAX_PAGES_TO_FETCH})
            elif sort_order == 2:
                sort_strategies.append({'sort': 'date_asc', 'pages': MAX_PAGES_TO_FETCH})
            elif sort_order == 3:
                sort_strategies.append({'sort': 'popular_desc', 'pages': MAX_PAGES_TO_FETCH})

            for strategy in sort_strategies:
                current_api_sort_param = strategy['sort']
                pages_to_fetch_for_this_strategy = strategy['pages']

                current_search_params_for_strategy = {
                    "word": search_query,
                    "search_target": 'exact_match_for_tags',
                    "sort": current_api_sort_param
                }

                for _ in range(pages_to_fetch_for_this_strategy):
                    if not current_search_params_for_strategy:
                        break
                    
                    _json_result = await asyncio.to_thread(
                        aapi.search_illust, 
                        **current_search_params_for_strategy
                    )

                    if _json_result and _json_result.illusts:
                        all_illusts.extend(_json_result.illusts)
                    
                    if _json_result and _json_result.next_url:
                        next_qs = aapi.parse_qs(_json_result.next_url)
                        if not next_qs: 
                            break
                        current_search_params_for_strategy = next_qs
                    else:
                        break 
            
            if not all_illusts:
                raise HTTPException(status_code=404, detail="未找到指定关键词的插画。")

            seen_illust_ids_current_request = set()
            unique_illusts_for_filtering = []
            for illust in all_illusts:
                if illust.id not in seen_illust_ids_current_request:
                    unique_illusts_for_filtering.append(illust)
                    seen_illust_ids_current_request.add(illust.id)
            
            if not unique_illusts_for_filtering:
                raise HTTPException(status_code=404, detail="处理后未找到有效插画（单次请求内去重后）。")

            non_recently_seen_illusts = []
            if unique_illusts_for_filtering:
                for illust in unique_illusts_for_filtering:
                    if not await is_illust_recently_seen(illust.id):
                        non_recently_seen_illusts.append(illust)
                
                if not non_recently_seen_illusts:
                    print(f"提示: 关键词 '{keyword}' 下所有符合初步条件的插画均在过去12小时内展示过。")
            
            unique_illusts_for_filtering = non_recently_seen_illusts

            if ai == 1:
                unique_illusts_for_filtering = [ill for ill in unique_illusts_for_filtering if ill.illust_ai_type != 2]
            elif ai == 2:
                unique_illusts_for_filtering = [ill for ill in unique_illusts_for_filtering if ill.illust_ai_type == 2]

            if not unique_illusts_for_filtering:
                ai_filter_message = "非AI生成的" if ai == 1 else "AI生成的" if ai == 2 else ""
                if ai_filter_message:
                    raise HTTPException(status_code=404, detail=f"未找到符合条件的{ai_filter_message}插画。")
                else:
                    raise HTTPException(status_code=404, detail="未找到符合条件的插画。")

            filtered_illusts = []
            for illust in unique_illusts_for_filtering: 
                if r18 == 1:
                    if illust.x_restrict != 0:
                        filtered_illusts.append(illust)
                elif r18 == 0:
                    if illust.x_restrict == 0:
                        filtered_illusts.append(illust)

            if not filtered_illusts:
                raise HTTPException(status_code=404, detail="未找到符合指定条件的插画。")

            selected_illust = random.choice(filtered_illusts)
            
            image_url_to_proxy = None
            if selected_illust.meta_single_page.get('original_image_url'):
                image_url_to_proxy = selected_illust.meta_single_page.get('original_image_url')
            elif selected_illust.meta_pages:
                if selected_illust.meta_pages[0].image_urls.get('original'):
                    image_url_to_proxy = selected_illust.meta_pages[0].image_urls.get('original')
                elif selected_illust.meta_pages[0].image_urls.get('large'):
                    image_url_to_proxy = selected_illust.meta_pages[0].image_urls.get('large')
                elif selected_illust.meta_pages[0].image_urls.get('medium'):
                    image_url_to_proxy = selected_illust.meta_pages[0].image_urls.get('medium')
            
            if image_url_to_proxy:
                await mark_illust_as_seen(selected_illust.id)
                async with httpx.AsyncClient() as client:
                    headers = {'Referer': 'https://www.pixiv.net/'}
                    try:
                        resp = await client.get(image_url_to_proxy, headers=headers, timeout=30.0)
                        resp.raise_for_status()

                        content_type = resp.headers.get('Content-Type', 'application/octet-stream')

                        return StreamingResponse(resp.iter_bytes(), media_type=content_type)
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code in [403, 404]:
                            raise HTTPException(status_code=e.response.status_code, detail=f"无法从Pixiv获取图片(id:{selected_illust.id})，链接可能已失效。")
                        else:
                            raise HTTPException(status_code=500, detail=f"代理请求Pixiv图片时发生网络错误: {e}")
                    except httpx.RequestError as e:
                        raise HTTPException(status_code=502, detail=f"代理请求Pixiv图片时网络连接失败: {e}")
            else:
                raise HTTPException(status_code=404, detail="选择的插画没有可用的图片链接。")

        except HTTPException as http_exc:
            raise http_exc
        except Exception as e:
            print(f"搜索Pixiv时出错: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"搜索Pixiv时出错: {str(e)}") 