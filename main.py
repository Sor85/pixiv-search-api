from fastapi import FastAPI, HTTPException, Query
from pixivpy3 import AppPixivAPI
import asyncio
import os
from typing import List, Optional
import random
from fastapi.responses import RedirectResponse, StreamingResponse
import mimetypes

app = FastAPI()

# Pixiv API凭证 
PIXIV_REFRESH_TOKEN = os.environ.get("PIXIV_REFRESH_TOKEN")

if not PIXIV_REFRESH_TOKEN:
    raise RuntimeError("PIXIV_REFRESH_TOKEN 环境变量未设置。")

aapi = AppPixivAPI()

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
):
    if not keyword:
        raise HTTPException(status_code=400, detail="关键词不能为空")

    tags = keyword.split(',')
    search_query = " ".join(tags)

    if r18 == 1:
        search_query += " R-18"

    try:
        # await authenticate_pixiv() # 如果希望每次请求都重新认证，请取消此行的注释

        json_result = await asyncio.to_thread(aapi.search_illust, search_query, search_target='exact_match_for_tags')
        
        if json_result.illusts:
            filtered_illusts = []
            for illust in json_result.illusts:
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
        else:
            raise HTTPException(status_code=404, detail="未找到指定关键词的插画。")

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        print(f"搜索Pixiv时出错: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"搜索Pixiv时出错: {str(e)}") 