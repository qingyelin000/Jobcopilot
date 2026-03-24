from mcp.server.fastmcp import FastMCP
from location_service import get_user_location_impl
from job_crawler_service import crawl_nearby_jobs_impl

mcp = FastMCP('Boss-Crawler-MCP')

@mcp.tool()
def get_user_location(consent: bool = False, user_city: str = "") -> str:
    return get_user_location_impl(consent=consent, user_city=user_city)

@mcp.tool()
def crawl_nearby_jobs(keyword: str, city_name: str, num_pages: int = 1) -> str:
    return crawl_nearby_jobs_impl(keyword, city_name, num_pages)

if __name__ == '__main__':
    print(' 开始启动 MCP 爬虫服务器 (SSE模式) ...')
    mcp.settings.host = '0.0.0.0'
    mcp.settings.port = 8001
    mcp.settings.transport_security.enable_dns_rebinding_protection = False
    mcp.run(transport='sse')


