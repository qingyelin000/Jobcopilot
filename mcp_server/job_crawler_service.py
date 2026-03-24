import json
import os
import random
from time import sleep

CITY_CODE_MAP = {
    '北京': '100010000',
    '上海': '100020000',
    '广州': '100030000',
    '深圳': '100040000',
    '杭州': '100080000',
    '全国': '100010000'
}

CITY_SITE_MAP = {
    '北京': 'beijing',
    '上海': 'shanghai',
    '广州': 'guangzhou',
    '深圳': 'shenzhen',
    '杭州': 'hangzhou',
}


def _human_pause(base: float = 1.3, jitter: float = 1.2):
    sleep(base + random.random() * jitter)


def _city_site_url(city_name: str) -> str:
    city_slug = CITY_SITE_MAP.get(city_name, 'shenzhen')
    return f'https://www.zhipin.com/citysite/{city_slug}/?ka=header-home'


def _detect_risk_page(page_title: str, page_html: str) -> str:
    markers = ["验证", "滑块", "人机", "异常访问", "captcha", "security check"]
    title = (page_title or "").lower()
    html = (page_html or "").lower()
    hit = [m for m in markers if m.lower() in title or m.lower() in html]
    return "、".join(hit)


def _extract_jobs_from_dom(page):
    script = """
    const cards = Array.from(document.querySelectorAll('.job-card-wrapper, .search-job-result .job-card-wrapper'));
    return cards.slice(0, 20).map(card => {
        const name = card.querySelector('.job-name, .job-title')?.textContent?.trim() || '';
        const salary = card.querySelector('.salary, .job-salary')?.textContent?.trim() || '';
        const company = card.querySelector('.company-name, .boss-name, .company-text')?.textContent?.trim() || '';
        const area = card.querySelector('.job-area, .job-area-wrapper')?.textContent?.trim() || '';
        const req = card.querySelector('.job-info, .job-limit')?.textContent?.trim() || '';
        return {
            '岗位名称': name,
            '工作地点': area,
            '薪资范围': salary,
            '公司名称': company,
            '职位要求': req
        };
    }).filter(item => item['岗位名称']);
    """
    result = page.run_js(script)
    return result if isinstance(result, list) else []


def crawl_nearby_jobs_impl(keyword: str, city_name: str, num_pages: int = 1) -> str:
    from DrissionPage import ChromiumPage, ChromiumOptions

    co = ChromiumOptions()
    co.headless(True)
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--disable-dev-shm-usage')
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument('--window-size=1440,900')
    co.set_argument('--lang=zh-CN,zh;q=0.9')
    co.set_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36')

    city_code = CITY_CODE_MAP.get(city_name, '100010000')
    diagnostics = {
        'keyword': keyword,
        'city_name': city_name,
        'city_code': city_code,
        'api_hits': 0,
        'dom_hits': 0,
        'risk_markers': '',
        'page_title': '',
        'preheat_url': '',
        'errors': []
    }
    dp = None

    try:
        dp = ChromiumPage(co)
        preheat_url = _city_site_url(city_name)
        search_urls = [
            f'https://www.zhipin.com/web/geek/job?query={keyword}&city={city_code}',
            f'https://www.zhipin.com/job_detail/?query={keyword}&city={city_code}&source=8',
            f'https://www.zhipin.com/web/geek/jobs?query={keyword}&city={city_code}',
        ]

        cookie_text = os.environ.get('BOSS_COOKIE', '').strip()
        if cookie_text:
            try:
                dp.set.cookies(cookie_text)
            except Exception as cookie_error:
                diagnostics['errors'].append('Cookie注入失败: ' + str(cookie_error))
        else:
            diagnostics['errors'].append('未设置 BOSS_COOKIE，命中登录页概率较高')

        dp.listen.start('joblist.json')

        diagnostics['preheat_url'] = preheat_url
        try:
            dp.get(preheat_url)
            _human_pause(2.0, 1.8)
            dp.run_js("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        except Exception as warm_error:
            diagnostics['errors'].append('预热访问失败: ' + str(warm_error))

        chosen_url = search_urls[0]
        for candidate_url in search_urls:
            for _ in range(2):
                dp.get(candidate_url)
                _human_pause(1.6, 1.4)

                page_title = getattr(dp, 'title', '') or ''
                try:
                    page_html = dp.html or ''
                except Exception:
                    page_html = ''

                risk = _detect_risk_page(page_title, page_html)
                lower_title = page_title.lower()
                is_login_page = ('登录' in page_title) or ('注册' in page_title) or ('login' in lower_title)

                chosen_url = candidate_url
                diagnostics['page_title'] = page_title
                diagnostics['risk_markers'] = risk

                if not is_login_page:
                    break

            if '登录' not in diagnostics['page_title'] and '注册' not in diagnostics['page_title'] and 'login' not in diagnostics['page_title'].lower():
                break

        diagnostics['entry_url'] = chosen_url

        jobs_list = []
        for _ in range(num_pages):
            dp.scroll.to_bottom()
            resp = dp.listen.wait(timeout=15)

            if resp:
                json_data = resp.response.body
                try:
                    job_list = json_data.get('zpData', {}).get('jobList', [])
                    for job in job_list:
                        work_location = job['cityName'] + '-' + job['areaDistrict'] + '-' + job['businessDistrict']
                        job_info = {
                            '岗位名称': job['jobName'],
                            '工作地点': work_location,
                            '薪资范围': job['salaryDesc'],
                            '公司名称': job['brandName'],
                            '职位要求': job['jobExperience'] + ' / ' + job['jobDegree']
                        }
                        jobs_list.append(job_info)
                    diagnostics['api_hits'] += len(job_list)
                except Exception:
                    continue
            sleep(2)

        if not jobs_list:
            try:
                dom_jobs = _extract_jobs_from_dom(dp)
                if dom_jobs:
                    jobs_list.extend(dom_jobs)
                    diagnostics['dom_hits'] = len(dom_jobs)
            except Exception as dom_error:
                diagnostics['errors'].append('DOM抓取失败: ' + str(dom_error))

        if not jobs_list:
            reason = '未抓到职位数据'
            if diagnostics['risk_markers']:
                reason = '疑似触发风控/验证码: ' + diagnostics['risk_markers']
            if diagnostics['errors']:
                reason += '；' + ' | '.join(diagnostics['errors'][:2])
            return (
                '未找到相关职位信息。'
                + '\n可能原因: ' + reason
                + '\n诊断: ' + json.dumps({
                    'city': diagnostics['city_name'],
                    'city_code': diagnostics['city_code'],
                    'preheat_url': diagnostics.get('preheat_url', ''),
                    'entry_url': diagnostics.get('entry_url', ''),
                    'api_hits': diagnostics['api_hits'],
                    'dom_hits': diagnostics['dom_hits'],
                    'page_title': diagnostics['page_title'],
                    'errors': diagnostics['errors'][:2]
                }, ensure_ascii=False)
            )

        result_text = '【声明】以下数据来自于Boss直聘，本工具仅提供检索。\n目前位于 ' + city_name + ' 的 ' + keyword + ' 职位如下：\n'
        for i, info in enumerate(jobs_list[:15]):
            result_text += str(i + 1) + '. ' + info['岗位名称'] + ' | 薪资: ' + info['薪资范围'] + ' | 公司: ' + info['公司名称'] + ' | 地点: ' + info['工作地点'] + ' | 要求: ' + info['职位要求'] + '\n'

        return result_text

    except Exception as e:
        return '爬虫执行失败: ' + str(e)
    finally:
        if dp is not None:
            try:
                dp.quit()
            except Exception:
                pass
