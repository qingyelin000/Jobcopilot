import requests
from bs4 import BeautifulSoup
headers = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/75.0.3770.100 Safari/537.36"}
def detail_url(url):
	html = requests.get(url,headers=headers).text
	soup = BeautifulSoup(html, 'lxml')
	title = soup.title.text
	job = title.split("招聘")[0]
	company_name = soup.select('.com_intro .com-name')[0].text.strip()
	adress = soup.select('.job_position')[0].text.strip()
	academic = soup.select('.job_academic')[0].text.strip()
	good_list = soup.select('.job_good_list')[0].text.strip()
	salary = soup.select(".job_money.cutom_font")[0].text.encode("utf-8")
	salary = salary.replace(b'\xee\x8b\x92',b"0")
	salary = salary.replace(b'\xee\x9e\x88',b"1")
	salary = salary.replace(b'\xef\x81\xa1',b"2")
	salary = salary.replace(b'\xee\x85\xbc',b"3")
	salary = salary.replace(b'\xef\x84\xa2',b"4")
	salary = salary.replace(b'\xee\x87\x99',b"5")
	salary = salary.replace(b'\xee\x9b\x91',b"6")
	salary = salary.replace(b'\xee\x94\x9d',b"7")
	salary = salary.replace(b'\xee\xb1\x8a',b"8")
	salary = salary.replace(b'\xef\x86\xbf',b"9")
	salary = salary.decode()
	print("：{} ：{} ：{} ：{} ：{} ：{} ".format(job,salary,company_name,adress,academic,good_list))
 
def job_url():
	for i in range(1,4):
		req = requests.get(f'https://www.shixiseng.com/interns?page={i}&type=intern&keyword=数据仓库&area=&months=&days=&degree=本科&official=&enterprise=&salary=-0&publishTime=&sortType=&city=全国&internExtend=',
			headers = headers)
		html = req.text
		soup = BeautifulSoup(html,'lxml')
		offers = soup.select('.intern-wrap.intern-item')
		for offer in offers:
			url = offer.select(" .f-l.intern-detail__job a")[0]['href']
			detail_url(url)
job_url()