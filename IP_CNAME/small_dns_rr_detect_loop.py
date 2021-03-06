# encoding:utf-8
'''
    功能：同时获取ip，ip的as信息，ip状态
'''
import sys
sys.path.append("..") # 回退到上一级目录
import database.mongo_operation
reload(sys)
sys.setdefaultencoding('utf-8')
mongo_conn = database.mongo_operation.MongoConn('172.29.152.151','new_mal_domain_profile')
import datetime
import schedule

"""多线程相关"""
import Queue
import threading
import time

"""插入时间"""
import datetime

"""ip，ns获取相关"""
import dns_rr.ip_dns_rr
import tldextract

"""编码处理"""
import encode_deal


"""地理位置相关"""
import ip2region.ip2Region
import ip2region.exec_ip2reg
searcher = ip2region.ip2Region.Ip2Region("ip2region/ip2region.db")

"""IP AS获取引入"""
import ASN.ip_as

"""IP state获取引入"""
import nmap_state.ip_nmap


domain_q = Queue.Queue()
res_q = Queue.Queue()
thread_num = 20
collection_name = 'domain_ip_cname'


def get_ip_rr_cname(check_domain):
    '''
    功能：DNS解析获取资源记录/获取ip地理位置信息
    '''

    global searcher

    g_ns, g_ips, g_cnames, ips_geo_list = [], [], [], []
    g_soa, g_txt, g_mx = [], [], []

    ## 提取domain+tld
    domain_tld = tldextract.extract(check_domain)
    if domain_tld.suffix == "":
        return []
    else:
        check_domain = domain_tld.domain+'.'+domain_tld.suffix

    fqdn_domain = 'www.' + check_domain  # 全域名
    print '查询的域名：',check_domain   # 在查询的域名

    try:
        """取消了被封装函数中的异常处理，所有异常在这里统一捕获处理"""
        dns_rr.ip_dns_rr.get_complete_dns_rr(fqdn_domain, g_ns, g_ips, g_cnames,g_soa, g_txt, g_mx)
    except Exception,e:
        """凡是捕获到异常的，均加入队列统一再获取一遍(因为有些总是出现异常(No address associated with hostname)，因此直接存空结果)"""
        print check_domain,str(e)

    for ip in g_ips:
        # 获取ip地理为值信息
        ip_geo_info = ip2region.exec_ip2reg.get_ip_geoinfo(searcher,ip)
        ips_geo_list.append(ip_geo_info)
    return g_cnames,g_ips,g_ns,ips_geo_list,g_soa, g_txt, g_mx



def get_asinfo(ips):
    """
        功能：具体为每个域名所对应的ip获取as信息
        :param ips:ip列表[ip1,ip2,.....](某个域名的ip列表)
        return domain,[{ASN:'',ASOWNER:'',...,},{ASN:'',ASOWNER:'',...,},...,{ASN:'',ASOWNER:'',...,}]
    """
    # ips为空列表时，直接返回[]
    if not ips:
        return []

    flag = True # 获取是否出现异常标志位
    as_info = []
    for ip in ips:
        try:
            std_asinfo = ASN.ip_as.get_std_asinfo(ip)
            as_info.append(std_asinfo)
        except Exception, e:
            # 出现异常则停止此域名的相关获取，否则会导致ip和as信息不对应
            flag = False
            break
    # 未出现异常的as信息获取加入结果队列
    if flag:
        # print as_info
        print 'as信息获取完成...'
        return as_info
    else:
        print '出现异常重新获取as信息...'
        return get_asinfo(ips)



def get_ip_state(ips):
    """
    功能：调用/nmap_state/ip_nmap中通过nmap扫描ip端口的函数
    :param ips:ip列表[ip1,ip2,.....](某个域名的ip列表)
    """
    # ips为空列表时，直接返回[]
    if not ips:
        return []

    flag = True # 获取是否出现异常标志位
    ip_state_list = []
    for ip in ips:
        try:
            print 'getting ' + ip + '  state ...'
            ip_state = nmap_state.ip_nmap.get_nmap_state(ip)
            ip_state_list.append(ip_state)
        except Exception, e:
            # 出现异常则停止此域名的相关获取，否则会导致ip和as信息不对应
            # print ip,str(e)
            flag = False
            break
    # 未出现异常的status信息获取加入结果队列
    if flag:
        # print ip_state_list
        print 'ip状态信息获取完成...'
        return ip_state_list
    else:
        print '出现异常重新获取ip状态...'
        return get_ip_state(ips)


def get_domains(limit_num = None):
    """
    从数据库中获取要初始获取数据的域名
    注：1 limit_num 控制是否获取一定量的域名
    """
    global mongo_conn
    global domain_q
    global collection_name

    fetch_data = mongo_conn.mongo_read(collection_name,{'visit_times':7},{'domain':True,'_id':False,'visit_times':True},limit_num)
    for item in fetch_data:
        domain_q.put([item['domain'],item['visit_times']])


def save_data():
    '''
    功能：存储ip相关信息
    '''
    global mongo_conn
    global res_q
    global collection_name

    while True:
        try:
            domain,res,changed = res_q.get(timeout=3600)
        except Queue.Empty:
            print '存储完成'
            break

        try:
            mongo_conn.mongo_any_update(collection_name,{'domain':domain},
                                        {
                                            '$inc':{'visit_times':1,'change_times':changed},
                                            '$push':{'domain_ip_cnames':{'$each':res}}
                                        })

            print domain + ' saved ...'
        except Exception,e:
            print domain + str(e)
            continue


def diff_list(list1,list2):
    '''
    功能：求两个列表差集(list1有但list2没有的元素)
    '''
    retD = list(set(list1).difference(set(list2)))
    return retD


def cmp_whether_chagne(check_domain,res):
    '''
    前后两次ip比对是否发生了变化
    '''
    global collection_name
    changed = 1 # 标志是否发生了变化

    fetch_data = mongo_conn.mongo_read(collection_name,{'domain':check_domain,},
                                                                 {'domain':True,
                                                                  'domain_ip_cnames':{'$slice':-1},
                                                                  '_id':False
                                                                 },limit_num=1
                                      )
    # 上一次的ip集合
    last_time_ips = fetch_data[0]['domain_ip_cnames'][0]['ips']
    # 这一次的ip集合
    ips = res['ips']
    new = diff_list(ips,last_time_ips)
    cut = diff_list(last_time_ips,ips)
    # 如果哦new和cut都是[],则说明没有发生变化
    if not new and not cut:
        changed = 0
    res['new'] = new
    res['cut'] = cut
    return changed


def run():
    '''
    功能：调用以上三个函数，一次性完次ip相关信息的获取
    '''
    global domain_q
    global res_q

    while not domain_q.empty():
        check_domain,last_visit_times = domain_q.get()
        print check_domain
        cnames,ips,ns,ips_geo_list,soa, txt, mx = get_ip_rr_cname(check_domain)
        ip_as = get_asinfo(ips)
        ip_state_list = get_ip_state(ips)
        insert_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        res = {
             'ips':ips,'NS':ns,'ip_geo':ips_geo_list,'cnames':cnames,
             'soa':soa,'txt':txt,'mx':mx,'ip_as':ip_as,
             'new':[],'cut':[],'ip_state':ip_state_list,'insert_time':insert_time
             }
        # 编码处理
        encode_deal.dict_encode_deal(res)
        changed = 0 # 是否发生改变标志，默认为0
        if last_visit_times != 0: # 不是第一次获取的时候，则通过比对来得到new,cut的内容
            changed = cmp_whether_chagne(check_domain,res)
        res['changed'] = changed
        res = [res]
        res_q.put([check_domain,res,changed])
    print '获取完成...'


def main():

    print 'start:  ', time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(time.time()))

    # 获取域名
    get_domains(limit_num = None)
    get_state_td = []
    for _ in range(thread_num):
        get_state_td.append(threading.Thread(target=run))
    for td in get_state_td:
        td.start()
    time.sleep(5)
    print 'save ip general info ...\n'
    save_db_td = threading.Thread(target=save_data)
    save_db_td.start()
    save_db_td.join()
    print 'end:   ', time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(time.time()))



if __name__ == '__main__':
    # schedule.every(1).minutes.do(main)
    # while True:
    #     schedule.run_pending()
    #     time.sleep(1)

    main()
