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
    keyword: str,
    r18: Optional[int] = Query(0, ge=0, le=1), # 0 表示非R18, 1 表示R18
    min_bookmarks: Optional[int] = Query(None, ge=0), # 最小收藏数筛选
    ai: Optional[int] = Query(0, ge=0, le=2), # 0: 全部, 1: 非AI, 2: AI
    sort_order: Optional[int] = Query(0, ge=0, le=3) # 0: 新/旧各半, 1: 按更新时间, 2: 按旧到新, 3: 按热门
):
    if not keyword:
        raise HTTPException(status_code=400, detail="关键词不能为空")

    tags = keyword.split(',')
    search_query = " ".join(tags)

    if r18 == 1:
        search_query += " R-18"

    try:
        await authenticate_pixiv()

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

        # 根据AI参数筛选
        if ai == 1: # 非AI
            unique_illusts_for_filtering = [ill for ill in unique_illusts_for_filtering if ill.illust_ai_type != 2]
        elif ai == 2: # AI
            unique_illusts_for_filtering = [ill for ill in unique_illusts_for_filtering if ill.illust_ai_type == 2]

        if not unique_illusts_for_filtering:
            ai_filter_message = "非AI生成的" if ai == 1 else "AI生成的" if ai == 2 else ""
            if ai_filter_message:
                raise HTTPException(status_code=404, detail=f"未找到符合条件的{ai_filter_message}插画。")
            else:
                raise HTTPException(status_code=404, detail="未找到符合条件的插画。")

        # 根据最小收藏数筛选
        if min_bookmarks is not None and min_bookmarks > 0: # 仅当 min_bookmarks > 0 时才筛选
            illusts_after_bookmark_filter = []
            for illust in unique_illusts_for_filtering:
                if illust.total_bookmarks >= min_bookmarks:
                    illusts_after_bookmark_filter.append(illust)
            
            if not illusts_after_bookmark_filter:
                raise HTTPException(status_code=404, detail=f"未找到收藏数大于等于 {min_bookmarks} 的插画。")
            unique_illusts_for_filtering = illusts_after_bookmark_filter

        # 从去重和收藏数筛选后的列表中进行R18筛选
        filtered_illusts = []
        for illust in unique_illusts_for_filtering: 
            if r18 == 1: # 用户需要R18内容
                if illust.x_restrict != 0:
                    filtered_illusts.append(illust)
            elif r18 == 0: # 用户需要SFW内容
                if illust.x_restrict == 0:
                    filtered_illusts.append(illust)

        if not filtered_illusts:
            raise HTTPException(status_code=404, detail="未找到符合指定条件的插画。")

        selected_illust = random.choice(filtered_illusts)
        
        image_url_to_redirect = None
        if selected_illust.meta_single_page.get('original_image_url'):
            image_url_to_redirect = selected_illust.meta_single_page.get('original_image_url')
        elif selected_illust.meta_pages:
            if selected_illust.meta_pages[0].image_urls.get('original'):
                image_url_to_redirect = selected_illust.meta_pages[0].image_urls.get('original')
            elif selected_illust.meta_pages[0].image_urls.get('large'):
                image_url_to_redirect = selected_illust.meta_pages[0].image_urls.get('large')
            elif selected_illust.meta_pages[0].image_urls.get('medium'):
                image_url_to_redirect = selected_illust.meta_pages[0].image_urls.get('medium')
        
        if not image_url_to_redirect:
            if selected_illust.image_urls.get('large'):
                image_url_to_redirect = selected_illust.image_urls.large
            elif selected_illust.image_urls.get('medium'):
                image_url_to_redirect = selected_illust.image_urls.medium

        if image_url_to_redirect:
            try:
                # 在成功获取并准备返回图片前，将其标记为已阅
                await mark_illust_as_seen(selected_illust.id)

                fetch_headers = {
                    "Referer": "https://www.pixiv.net/"
                }
                
                image_response = await asyncio.to_thread(
                    aapi.requests.get,
                    image_url_to_redirect,
                    headers=fetch_headers,
                    stream=True
                )

                if image_response.status_code == 200:
                    media_type = image_response.headers.get("Content-Type")
                    if not media_type:
                        media_type, _ = mimetypes.guess_type(image_url_to_redirect)
                        if not media_type:
                            media_type = "application/octet-stream"
                    response_headers = {
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "Pragma": "no-cache",
                        "Expires": "0"
                    }

                    return StreamingResponse(image_response.iter_content(chunk_size=8192), 
                                             media_type=media_type,
                                             headers=response_headers)
                else:
                    error_detail = f"从Pixiv服务器获取图片失败。状态码: {image_response.status_code}。URL: {image_url_to_redirect}"
                    print(error_detail)
                    try:
                        pixiv_error_content = await asyncio.to_thread(image_response.text)
                        print(f"Pixiv错误内容: {pixiv_error_content[:500]}")
                    except Exception:
                        pass
                    raise HTTPException(status_code=502, detail=error_detail)
            except Exception as fetch_exc:
                print(f"图片获取过程中发生异常: {fetch_exc}")
                import traceback
                traceback.print_exc()
                raise HTTPException(status_code=500, detail=f"获取图片错误: {str(fetch_exc)}")
        else:
            raise HTTPException(status_code=404, detail="未找到所选插画的合适图片URL。")

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        print(f"搜索Pixiv时出错: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"搜索Pixiv时出错: {str(e)}") 