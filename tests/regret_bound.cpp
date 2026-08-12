// regret.cpp — 零配额上界工具: (1) 面值分档的量化后悔上界 (2) 曼4 四角的可得金上界
// 口径: 对每个单位轮, 用与 player.cpp 完全相同的阻挡判定重建候选集, 穷举 48 条候选的**精确面值**
// 路径金子和取 max = 该轮上界; 再回放 .so 取它实际选的动作、按引擎规则模拟得实际所得; 差 = 后悔。
// ⚠ 这是**当轮拾取**的上界, 不含位置的期权价值 ⇒ 只能用来「关掉一条不值得做的刀」, 不能用来承诺收益。
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <dlfcn.h>
#include "../src/game_api.h"
typedef GameOutput (*FN)(const GameInput*);
static const int DR[5]={-1,1,0,0,0}, DC[5]={0,0,-1,1,0};
struct P { int len, aa[3], di[3], dj[3]; };
int main(int argc,char**argv){
  std::vector<P> ps;
  for(int fam=0; fam<4; ++fam){
    int len = fam<=1?3:(fam==2?2:1);
    for(int a0=0;a0<4;a0++) for(int a1=0;a1<4;a1++){
      if(len>=2 && a1==(a0^1)) continue;
      if(len<2 && a1!=0) continue;
      for(int a2=0;a2<4;a2++){
        if(len>=3 && a2==(a1^1)) continue;
        if(len<3 && a2!=0) continue;
        int aa[3]={a0,a1,a2}, r=0,c=0; bool ok=true; P p; p.len=len;
        for(int s=0;s<len;s++){ r+=DR[aa[s]]; c+=DC[aa[s]];
          if(r<-2||r>2||c<-2||c>2){ok=false;break;}
          p.di[s]=r; p.dj[s]=c; }
        if(!ok) continue;
        int man=(r<0?-r:r)+(c<0?-c:c);
        if(fam==0&&man!=3) continue;
        if(fam==1&&man==3) continue;
        p.aa[0]=a0;p.aa[1]=a1;p.aa[2]=a2;
        ps.push_back(p);
      }
    }
  }
  void* h=dlopen(argv[1],RTLD_NOW); FN f=(FN)dlsym(h,"moveDecision");
  FILE* fp=fopen(argv[2],"rb"); fseek(fp,0,SEEK_END); long sz=ftell(fp); fseek(fp,0,SEEK_SET);
  std::vector<char> buf(sz); if(fread(buf.data(),1,sz,fp)!=(size_t)sz) return 1; fclose(fp);
  const int REC=sizeof(GameInput); int n=sz/REC;
  long act=0, opt=0, ur=0, regret_ur=0, cornerN=0, cornerG=0, cornerUR=0;
  for(int i=0;i<n;i++){
    const GameInput* in=(const GameInput*)(buf.data()+(long)i*REC);
    GameOutput o=f(in);
    for(int u=0;u<2;u++){
      int sr=in->my_units[u].row, sc=in->my_units[u].col;
      int mr=in->my_units[1-u].row, mc=in->my_units[1-u].col;
      auto blocked=[&](int rr,int cc)->bool{
        if(rr<0||rr>16||cc<0||cc>16) return true;
        int v=in->grid[rr][cc];
        if(v<0) return true;
        if(rr==mr&&cc==mc) return true;
        return false; };
      auto val=[&](int rr,int cc,int taken)->int{
        int v=in->grid[rr][cc];
        for(int t=0;t<taken;t++){ int a=(65*v+99)/100; v-=a; }
        return v>0 ? (65*v+99)/100 : 0; };
      // 上界: 穷举候选(精确面值)
      int best=0;
      for(size_t k=0;k<ps.size();k++){
        bool okp=true; int g=0;
        for(int s=0;s<ps[k].len;s++){ int rr=sr+ps[k].di[s], cc=sc+ps[k].dj[s];
          if(blocked(rr,cc)){okp=false;break;} g+=val(rr,cc,0); }
        if(okp && g>best) best=g;
      }
      // 实际: 回放 .so 的动作
      int r=sr,c=sc,g=0; int cnt[5][5]={};
      for(int s=0;s<3;s++){ int a=o.actions[u*3+s]; if(a==4) continue;
        int nr=r+DR[a], nc=c+DC[a];
        if(blocked(nr,nc)) continue;
        int di=nr-sr+2, dj=nc-sc+2; int taken=0;
        if(di>=0&&di<5&&dj>=0&&dj<5){ taken=cnt[di][dj]; cnt[di][dj]++; }
        g+=val(nr,nc,taken); r=nr;c=nc; }
      act+=g; opt+=best; ur++; if(best>g) regret_ur++;
      // 曼4 四角
      int cg=0,cn=0;
      for(int di=-2;di<=2;di+=4) for(int dj=-2;dj<=2;dj+=4){
        int rr=sr+di, cc=sc+dj;
        if(rr<0||rr>16||cc<0||cc>16) continue;
        int v=in->grid[rr][cc];
        if(v>0){ cn++; cg+=(65*v+99)/100; } }
      cornerN+=cn; cornerG+=cg; if(cn) cornerUR++;
    }
  }
  printf("%-26s uround=%ld | 实际当轮金 %ld  上界(穷举精确面值) %ld  后悔 %ld (%.1f%%, 有后悔的单位轮 %.1f%%)"
         " | 曼4四角: 有金单位轮 %.1f%%, 平均可得 %.3f 金/单位轮, 全局 %ld\n",
    argv[3]?argv[3]:argv[1], ur, act, opt, opt-act, 100.0*(opt-act)/(act?act:1),
    100.0*regret_ur/ur, 100.0*cornerUR/ur, (double)cornerG/ur, cornerG);
  return 0;
}
