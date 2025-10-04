import pandas as pd
import requests
import time
from bs4 import BeautifulSoup as bs
import os
import re
import pytesseract
from pdf2image import convert_from_bytes
from openai import OpenAI
from dotenv import load_dotenv
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
pushover_api = os.getenv("PUSHOVER_API")
pushover_key = os.getenv("PUSHOVER_KEY")

pdfmetrics.registerFont(TTFont("DejaVuSans", "./fonts/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DejaVuItalic", "./fonts/DejaVuSerif-Italic.ttf"))
pdfmetrics.registerFont(TTFont("DejaVuBold", "./fonts/DejaVuSans-Bold.ttf"))

styles = getSampleStyleSheet()

styles.add(ParagraphStyle(name="TitlePL", parent=styles["Title"], fontName="DejaVuBold"))
styles.add(ParagraphStyle(name="Heading2PL", parent=styles["Heading2"], fontName="DejaVuBold"))
styles.add(ParagraphStyle(name="Heading3PL", parent=styles["Heading3"], fontName="DejaVuSans"))
styles.add(ParagraphStyle(name="NormalPL", parent=styles["Normal"], fontName="DejaVuSans"))
styles.add(ParagraphStyle(name="ItalicPL", parent=styles["Italic"], fontName="DejaVuItalic"))

def get_page(url):
    page = requests.get(url)
    soup = bs(page.content, 'html.parser')
    return soup

def read_pdf_image(url):
    response = requests.get(url)
    response.raise_for_status()
    pdf_bytes = response.content
    pages = convert_from_bytes(pdf_bytes)

    pdf_text = ""
    for page in pages:
        page_text = pytesseract.image_to_string(page, lang="pol")
        pdf_text += page_text
    return pdf_text

def clean(string):
    return ' '.join(string.replace('\n', ' ').split())

def split_summary(string):
    return [i.strip() for i in string.split('>') if i.strip()]

def clean_title(title):
    title = re.sub(r"\(.*?\)", "", title)
    title = re.sub(r"^Druk Nr.*?- ", "", title)
    return title.strip()

def summary(tekst):
    response = client.chat.completions.create(
        model='gpt-4o-mini',
        messages=[
            {'role': 'system', 'content': 'Jesteś asystentem, który przygotowuje krótkie streszczenia uchwał gminnych.'},
            {'role': 'user', 'content': f'Streść poniższy projekt uchwały w kilku punktach. Nie używaj żadnych nagłówków, wypisz pojedyncze zdania, zaczynając każde od znaku ">". Skup się na jej celu i uzasadnieniu, pomijając kwestie formalne.\n\n{tekst}'}
        ],
        max_tokens=800
    )
    return response.choices[0].message.content

def bip_to_pdf(file_path, dataframe):
    
    story = []

    story.append(Paragraph(f"Podsumowanie projektów uchwał Rady Gminy Dąbrowa - {time.strftime('%d.%m.%Y')}:", styles["TitlePL"]))
    story.append(Spacer(1, 20))
    
    sesje = dataframe['Sesja'].unique()

    for sesja in sesje:
        extract = dataframe[dataframe['Sesja']==sesja]
        
        story.append(Paragraph(sesja, styles['Heading2PL']))
        story.append(Spacer(1, 10))
        for name in extract['Nazwa'].values:
            link = extract[extract['Nazwa']==name]['Link'].iloc[0]
            streszczenie = extract[extract['Nazwa']==name]['Streszczenie'].iloc[0]
            
            story.append(Paragraph(f'<a href="{link}">{name}</a>', styles['Heading3PL']))
            story.append(Spacer(1, 8))
            
            punkty = [ListItem(Paragraph(punkt, styles["NormalPL"])) for punkt in streszczenie]
            story.append(ListFlowable(punkty, bulletType="bullet", start="•"))
            story.append(Spacer(1, 10))

    story.append(Paragraph('Wygenerowano automatycznie przez BIP Watcher', styles['ItalicPL']))

    doc = SimpleDocTemplate(file_path, pagesize=A4)
    doc.build(story)


def retry_request(func, max_attempts=5, delay=5):
    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except:
            if attempt < max_attempts:
                time.sleep(delay)
            else:
                response = requests.post(
                    'https://api.pushover.net/1/messages.json',
                    data={
                        'token': pushover_api,
                        'user': pushover_key,
                        'title': 'BIP Watcher - Gmina Dąbrowa',
                        'message': f"Wystąpił błąd przy próbie połączenia z serwerem. Kolejna próba zostanie podjęta jutro. Jeżeli problem będzie się powtarzał, proszę skontaktować się z BIP watcher. {time.strftime('%d.%m.%Y')}",
                    })
                raise RuntimeError(f'Nie udało połączyć się z zewnętrznym serwerem po {attempt} próbach.')

projekty = pd.read_csv('./projekty.csv')

url = 'https://bip.gminadabrowa.pl/8446/2899/projekty-2024-2029.html?Page=1'
soup1 = retry_request(lambda:get_page(url))

pname = [i.a.get_text() for i in soup1.find(attrs={'class':'pageOnPage'}).find_all('h2')]
plink = [j.a.get('href') for j in soup1.find(attrs={'class':'pageOnPage'}).find_all('h2')]
projdf = pd.DataFrame(list(zip(pname, plink)), columns=['Sesja', 'Link'])

newprojdf = pd.DataFrame(columns=['Sesja','Link'])
for i in projdf['Sesja'].values:
    if i not in projekty['Sesja'].values:
        newprojdf.loc[len(newprojdf)]=[i, projdf[projdf['Sesja']==i]['Link'].values[0]]
        newprojdf.index +=1
        newprojdf.sort_index(inplace=True)
    else:
        pass

if newprojdf.empty:
    pass
else:
    projlistdf = pd.DataFrame(columns=['Sesja','Nazwa','Link','Streszczenie'])
    
    for link in newprojdf['Link'].values:
        soup2 = retry_request(lambda:get_page(link))
        projlist = soup2.find(attrs={'class':'bip-page__content'}).find_all('a')
        
        for p in projlist:
            pdftext = read_pdf_image(p.get('href'))
            pdftext = clean(pdftext)
            pdfsummary = retry_request(lambda:summary(pdftext))
            pdfsummary = clean(pdfsummary)
            pdfsummary = split_summary(pdfsummary)
            title = clean_title(p.get_text())
            href = p.get('href')   
            sesja = newprojdf[newprojdf['Link'] == link]['Sesja'].values
            
            if len(sesja) == 0:
                sesja_value = None
            else:
                sesja_value = sesja[0]
            
            projlistdf.loc[len(projlistdf)]=[sesja_value, title, href,pdfsummary]
            
    rpath = f"raport {time.strftime('%d.%m.%Y')}.pdf"
    bip_to_pdf(rpath,projlistdf)

    with open(rpath, "rb") as raport:
        response = requests.post(
            'https://api.pushover.net/1/messages.json',
            data={
                'token': pushover_api,
                'user': pushover_key,
                'title': 'BIP Watcher - Gmina Dąbrowa',
                'message': f"Projekty Uchwał Rady Gminy Dąbrowa 📄 - {time.strftime('%d.%m.%Y')}",
            },
            files={'attachment': (os.path.basename(rpath), raport, 'application/pdf')}
        )

    projekty = pd.concat([newprojdf,projekty]).reset_index(drop=True)
    projekty.to_csv('projekty.csv', index=False)