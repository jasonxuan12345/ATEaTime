"""Bounded vector report primitives shared by Site Diff and Outlier PDFs."""
import math
import numpy as np
from reportlab.lib.colors import HexColor
from deepyield.compute.release_cgm_pdf import Slides as BaseSlides, WIDTH, HEIGHT, COLORS, BLUE, LIGHT
from deepyield.compute.release_control import checkpoint
from deepyield.compute.release_review_data import fmt

PALETTE=['#0072B2','#E69F00','#009E73','#CC79A7','#D55E00','#56B4E9','#8C564B','#7B61A8']


class Slides(BaseSlides):
    def header(self,title,stamp,page,total,badge=None):
        super().header(title,stamp,page,total,badge)
        if badge is None:
            self.box(25,584,820,22,BLUE)
            self.text(30,592,stamp,9,'#FFFFFF',width=800)


def chunks(values,n):
    return [values[i:i+n] for i in range(0,len(values),n)] or [[]]


def sample(points,limit):
    if len(points)<=limit:return points
    return [points[i] for i in np.linspace(0,len(points)-1,limit,dtype=int)]


def limits(values):
    clean=[float(v) for v in values if v is not None and math.isfinite(float(v))]
    low,high=(min(clean),max(clean)) if clean else (0.,1.)
    delta=(high-low)*.08 or max(abs(low)*.02,.5)
    return low-delta,high+delta


def note(slide,value,x,y,w,h):
    return slide.remark(str(value),x,w,top=y+h-2,max_height=h)


def context(slide,value,y=551):
    slide.text(30,y,value,9,width=1065)
    slide.c.textAnnotation(str(value),Rect=(1110,y-3,1122,y+9),Name='Comment')


def kpis(slide,values):
    for i,(label,value) in enumerate(values):
        x=30+i*278
        slide.box(x,440,258,61,LIGHT,5)
        slide.text(x+10,483,label,10,BLUE,True,width=238)
        slide.text(x+10,455,value,20,BLUE,True,width=238)


class Panel:
    def __init__(self,s,rect,title,xvalues,yvalues,xlabel='',ylabel='',xticks=True,yticks=True):
        self.s=s;self.rect=rect
        x,y,w,h=rect
        s.box(x,y,w,h,'#FFFFFF')
        s.text(x+8,y+h-15,title,11,BLUE,True,width=w-16)
        self.x0,self.y0,self.w,self.h=x+54,y+34,w-72,h-72
        self.xlim,self.ylim=limits(xvalues),limits(yvalues)
        for v in np.linspace(*self.ylim,4):
            yy=self.yy(v);s.line(self.x0,yy,self.x0+self.w,yy,'#DDE4E9',width=.4)
            if yticks:s.text(x+2,yy-3,fmt(float(v)),7,width=48)
        if xticks:
            for v in np.linspace(*self.xlim,4):s.text(self.xx(v)-17,self.y0-12,fmt(float(v)),7,width=48)
        s.line(self.x0,self.y0,self.x0+self.w,self.y0,BLUE)
        s.text(self.x0+self.w/2-65,y+4,xlabel,8,width=180)
        s.text(x+9,y+h-29,ylabel,7,width=w-20)
    def xx(self,v):return self.x0+(float(v)-self.xlim[0])/(self.xlim[1]-self.xlim[0])*self.w
    def yy(self,v):return self.y0+(float(v)-self.ylim[0])/(self.ylim[1]-self.ylim[0])*self.h
    def clipped(self,draw):
        c=self.s.c;c.saveState();p=c.beginPath();p.rect(self.x0,self.y0,self.w,self.h);c.clipPath(p,stroke=0);draw();c.restoreState()
    def dots(self,points,color,radius=1.1):
        self.s.c.setFillColor(HexColor(color))
        self.clipped(lambda:self.s.dots(((self.xx(x),self.yy(y)) for x,y in points),radius))
    def line(self,xs,ys,color,width=1,dash=None):
        def draw():
            c=self.s.c;c.setStrokeColor(HexColor(color));c.setLineWidth(width);c.setDash(dash or []);p=c.beginPath()
            for i,(x,y) in enumerate(zip(xs,ys)):
                if i%2048==0:checkpoint()
                (p.moveTo if i==0 else p.lineTo)(self.xx(x),self.yy(y))
            c.drawPath(p)
        self.clipped(draw)
    def polygon(self,points,color):
        def draw():
            c=self.s.c;c.setFillColor(HexColor(color));p=c.beginPath()
            for i,(x,y) in enumerate(points):(p.moveTo if i==0 else p.lineTo)(self.xx(x),self.yy(y))
            p.close();c.drawPath(p,fill=1,stroke=0)
        self.clipped(draw)
    def label(self,x,y,value,color='#344E60'):
        self.s.text(min(self.xx(x),self.x0+self.w-60),min(self.yy(y),self.y0+self.h-9),value,7,color,width=65)
    def specs(self,low,high):
        for v,label in ((low,'LSL'),(high,'USL')):
            if v is not None:self.line([self.xlim[0],self.xlim[1]],[v,v],'#CC3333',.8,[3,2]);self.label(self.xlim[0],v,label,'#CC3333')
