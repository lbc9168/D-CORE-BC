import requests
from bs4 import BeautifulSoup
import pandas as pd
import re
import time
from datetime import datetime

# --- 配置参数 ---
BASE_URL = "https://news.goalfore.cn/topstories/project"
START_DATE = datetime(2024, 1, 1)
END_DATE = datetime(2025, 9, 30)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
# 关键词列表（只要包含其中任意一个，就保留）
KEYWORDS = ["能源", "电力", "MW", "GW", "风电", "水电", "光伏", "储能"]

def get_article_details(url):
    """进入详情页爬取作者和正文"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.encoding = response.apparent_encoding # 自动识别编码，防止中文乱码
        soup = BeautifulSoup(response.text, 'html.parser')

        # 1. 提取标题：div class="list_top" 下的 h3
        title_tag = soup.select_one('div.list_top h3')
        title = title_tag.get_text(strip=True) if title_tag else "未找到标题"

        # 2. 提取正文：div class="detail-content"
        content_div = soup.find('div', class_='detail-content')
        content = content_div.get_text(separator='\n', strip=True) if content_div else "未找到正文"

        # 3. 提取作者和时间：div class="pull-left"
        author = "NA"
        publish_time = "NA"
        
        # 策略：先找到 list_top 这个大容器
        list_top_div = soup.find('div', class_='list_top')
        if list_top_div:
            # 在 list_top 容器内寻找包含元数据的 pull-left
            # 注意：这里我们找 list_top 下方的所有 pull-left，并排除掉面包屑
            meta_candidates = list_top_div.find_all('div', class_='pull-left')
            
            for candidate in meta_candidates:
                text = candidate.get_text(strip=True)
                # 排除掉面包屑导航的干扰项
                if "首页" in text or "走出去" in text:
                    continue
                
                # 如果包含日期格式，这才是我们要找的
                if re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', text):
                    raw_text = text
                    # 提取日期
                    date_match = re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', raw_text)
                    if date_match:
                        publish_time = date_match.group()
                    
                    # 提取作者/来源
                    author = raw_text.replace("发布时间：", "").replace(publish_time, "").replace("来源：", "").strip()
                    break # 找到正确的就退出循环

        # --- 打印结果 ---
        
        print(f"【标题】: {title}")
        print(f"【作者】: {author}")
        print(f"【时间】: {publish_time}")
        print(f"【正文预览】: {content[:100]}...")
        

        return title, author, publish_time, content
           
    except Exception as e:
        print(f"运行出错: {e}")
        return None



def main():
    all_data = []
    page = 1
    stop_crawling = False
    consecutive_old_news_count = 0 # 计数器：连续遇到旧新闻的次数
    last_valid_date = None # 用于存储上一条有效的日期数据

    print(f">>> 任务启动：爬取 {START_DATE.date()} 至 {END_DATE.date()} 的新闻")
    print(f">>> 关键词过滤：{KEYWORDS}")

    while not stop_crawling:
        # 1. 拼接分页 URL
        list_url = f"{BASE_URL}?page={page}"
        print(f"\n正在扫描第 {page} 页...")
        
        try:
            res = requests.get(list_url, headers=HEADERS, timeout=10)
            soup = BeautifulSoup(res.text, 'html.parser')
            
            # 获取列表项 (li class="list")
            list_items = soup.find_all('li', class_='list')
            
            if not list_items:
                print("未发现更多条目，停止爬取。")
                break

            for item in list_items:
                # --- A. 提取日期 (在列表页的 pull-left 中) ---
                # 注意：这里需要在 li 标签下找 pull-left
                meta_div = item.find('div', class_='pull-left')
                if not meta_div:
                    continue
                
                meta_text = meta_div.get_text(strip=True)
                # 使用正则提取 YYYY-MM-DD
                date_match = re.search(r'\d{4}-\d{2}-\d{2}', meta_text)
                
                if not date_match:
                    continue # 没找到日期，跳过
                
                item_date = datetime.strptime(date_match.group(), '%Y-%m-%d')
                
                # --- B. 日期逻辑判断 ---
                # 情况1：新闻太新 (比 END_DATE 晚) -> 跳过，继续看下一条
                if item_date > END_DATE:
                    consecutive_old_news_count = 0 # 重置计数器
                    continue 
                
                # 情况2：新闻太旧 (比 START_DATE 早) -> 计数，不要立刻停 (防止置顶帖干扰)
                if item_date < START_DATE:
                    consecutive_old_news_count += 1
                    print(f"  [跳过] 日期过早: {item_date.date()} (连续第{consecutive_old_news_count}条)")
                    # 如果连续 5 条都是旧新闻，说明真的已经翻过头了，可以安全停止
                    if consecutive_old_news_count >= 5:
                        print(">>> 连续检测到多条旧新闻，判定已超出日期范围，停止任务。")
                        stop_crawling = True
                        break
                    continue
                
                # 情况3：日期符合要求 -> 重置旧新闻计数器，准备爬取
                consecutive_old_news_count = 0
                
                # --- C. 提取链接并进入详情页 ---
                title_tag = item.find('a', href=True)
                if not title_tag:
                    continue
                
                title = title_tag.get_text(strip=True)
                link = title_tag['href']
                
                # 处理相对路径
                if not link.startswith('http'):
                    link = "https://news.goalfore.cn" + link
                
                print(f"  [读取中] {title} ({item_date.date()})")
                
                # 调用你已经写好的详情页爬取函数
                # 注意：get_article_details 需要返回 (title, author, time, content) 4个变量
                # 这里假设你之前的函数返回的是 tuple
                try:
                    # 使用你提供的 get_article_details 函数
                    # 注意：为了让逻辑通顺，我假设该函数返回一个字典或元组，这里适配你的 Crawler.py
                    d_title, d_author, d_time, d_content = get_article_details(link)

                    
                    # --- 【侦探代码开始】 ---
                    print(f"    [调试] 正文长度: {len(d_content)}")
                    if d_content == "NA":
                        print("    [调试警告] 正文提取失败，内容为 NA，因此跳过。")
                        continue
                    
                    # 打印正文的前50个字，看看机器到底读到了什么
                    print(f"    [调试] 机器看到的正文前50字: {d_content[:50].replace('\n', '')}")
                    # --- 【侦探代码结束】 ---

                    # --- 关键词检查 ---
                    is_relevant = any(k.upper() in d_content.upper() for k in KEYWORDS)
                    
                    if d_content == "NA":
                        continue

                    # --- D. 关键词过滤 (核心新增逻辑) ---
                    # 检查 content 中是否包含 KEYWORDS 里的任意一个词
                    # 使用 upper() 是为了同时匹配 mw, MW, gw, GW
                    is_relevant = any(k.upper() in d_content.upper() for k in KEYWORDS)
                    
                    if is_relevant:
                        # --- 日期补丁：如果当前日期为 NA，使用上一条的日期 ---
                        if d_time == "NA" and last_valid_date is not None:
                            d_time = last_valid_date
                            print(f"    -> [补丁] 日期为NA，已自动填入上一条日期: {d_time}")
                        elif d_time != "NA":
                            last_valid_date = d_time # 更新最后一个有效日期
                        
                        print("    -> [保留] 包含关键词，已存入列表")
                        all_data.append({
                            "发布日期": d_time, # 使用详情页更精准的时间，或者用 item_date.strftime('%Y-%m-%d')
                            "文章标题": d_title,
                            "正文内容": d_content,
                            "文章作者": d_author,
                            "原文链接": link
                        })
                    else:
                        print("    -> [丢弃] 正文不含关键词")
                        
                except Exception as e:
                    print(f"    -> [错误] 处理详情页失败: {e}")
                
                # 礼貌性延时
                time.sleep(1)

            # 翻页
            page += 1
            time.sleep(1) # 翻页间隙也休息一下

        except Exception as e:
            print(f"列表页请求错误: {e}")
            break

    # --- E. 保存结果 ---
    if all_data:
        df = pd.DataFrame(all_data)
        file_name = f"Energy_News_Filtered_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        df.to_excel(file_name, index=False)
        print(f"\n>>> 成功！共抓取并保留 {len(all_data)} 条有效新闻，已保存至 {file_name}")
    else:
        print("\n>>> 未找到符合条件的新闻。")

# 确保你的 Crawler.py 里有 get_article_details 定义，并在最后调用 main()
if __name__ == "__main__":
    main()