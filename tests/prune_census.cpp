// prune.cpp — 复刻 player.cpp 的候选剔除口径, 统计含墙窗口下各族的存活率(零配额)
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include "../src/game_api.h"
static const int DR[4]={-1,1,0,0}, DC[4]={0,0,-1,1};
struct P { unsigned long long cells; int len; int fam; int aa[3]; };
int main(int argc,char**argv){
  // 生成 48 条候选(与 player.cpp PathT 同规则)
  std::vector<P> ps;
  for(int fam=0; fam<4; ++fam){
    int len = fam<=1?3:(fam==2?2:1);
    for(int a0=0;a0<4;a0++) for(int a1=0;a1<4;a1++){
      if(len>=2 && a1==(a0^1)) continue;
      if(len<2 && a1!=0) continue;
      for(int a2=0;a2<4;a2++){
        if(len>=3 && a2==(a1^1)) continue;
        if(len<3 && a2!=0) continue;
        int aa[3]={a0,a1,a2}, r=0,c=0; bool ok=true; unsigned long long cm=0;
        for(int s=0;s<len;s++){ r+=DR[aa[s]]; c+=DC[aa[s]];
          if(r<-2||r>2||c<-2||c>2){ok=false;break;}
          cm |= 1ULL<<(8*(r+3)+(c+2)); }
        if(!ok) continue;
        int man=(r<0?-r:r)+(c<0?-c:c);
        if(fam==0&&man!=3) continue;
        if(fam==1&&man==3) continue;
        P p; p.cells=cm; p.len=len; p.fam=fam<=1?0:(fam==2?1:2);
        p.aa[0]=aa[0];p.aa[1]=aa[1];p.aa[2]=aa[2];
        ps.push_back(p);
      }
    }
  }
  FILE* f=fopen(argv[1],"rb"); fseek(f,0,SEEK_END); long sz=ftell(f); fseek(f,0,SEEK_SET);
  std::vector<char> buf(sz); if(fread(buf.data(),1,sz,f)!=(size_t)sz) return 1; fclose(f);
  const int REC=sizeof(GameInput); int n=sz/REC;
  long ur=0, Lempty=0, Sempty=0, allempty=0, survL=0, survS=0, survO=0, totL=0;
  long candsum=0;
  for(int i=0;i<n;i++){
    const GameInput* in=(const GameInput*)(buf.data()+(long)i*REC);
    for(int u=0;u<2;u++){
      int sr=in->my_units[u].row, sc=in->my_units[u].col;
      unsigned long long bd=0;
      for(int di=-2;di<=2;di++) for(int dj=-2;dj<=2;dj++){
        int rr=sr+di, cc=sc+dj;
        int b=8*(di+3)+(dj+2);
        if(rr<0||rr>16||cc<0||cc>16){ bd|=1ULL<<b; continue; }
        int v=in->grid[rr][cc];
        if(v<0) bd|=1ULL<<b;
      }
      { int tr=in->my_units[1-u].row-sr+2, tc=in->my_units[1-u].col-sc+2;
        if((unsigned)tr<5u&&(unsigned)tc<5u) bd|=1ULL<<(8*(tr+1)+tc); }
      int cl=0,cs=0,co=0;
      for(size_t k=0;k<ps.size();k++){
        if(ps[k].cells & bd) continue;
        if(ps[k].fam==0) cl++; else if(ps[k].fam==1) cs++; else co++;
      }
      ur++; totL+=32; survL+=cl; survS+=cs; survO+=co; candsum+=cl+cs+co;
      if(cl==0) Lempty++;
      if(cl==0&&cs==0) Sempty++;
      if(cl==0&&cs==0&&co==0) allempty++;
    }
  }
  printf("%-26s uround=%ld  L存活=%.1f/32(%.1f%%) S存活=%.1f/12 O存活=%.1f/4  候选均值=%.1f/48 | L族全灭=%.2f%% L+S全灭=%.2f%% 全灭=%.3f%%\n",
    argv[2]?argv[2]:argv[1], ur, (double)survL/ur, 100.0*survL/totL, (double)survS/ur, (double)survO/ur,
    (double)candsum/ur, 100.0*Lempty/ur, 100.0*Sempty/ur, 100.0*allempty/ur);
  return 0;
}
