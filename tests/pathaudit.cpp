// audit.cpp — 用录制输入回放 .so, 审计「请求动作」的合法性与新格数(零平台配额)
// 关键点: 日志只记**生效**动作, 无法区分主动 STAY 与撞墙降级 ⇒ 必须回放 .so 拿请求动作。
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <dlfcn.h>
#include "../src/game_api.h"
typedef GameOutput (*FN)(const GameInput*);
static const int DR[5]={-1,1,0,0,0}, DC[5]={0,0,-1,1,0};
int main(int argc,char**argv){
  void* h=dlopen(argv[1],RTLD_NOW); if(!h){printf("dlopen: %s\n",dlerror());return 1;}
  FN f=(FN)dlsym(h,"moveDecision");
  FILE* fp=fopen(argv[2],"rb"); if(!fp){perror("bin");return 1;}
  fseek(fp,0,SEEK_END); long sz=ftell(fp); fseek(fp,0,SEEK_SET);
  std::vector<char> buf(sz); if(fread(buf.data(),1,sz,fp)!=(size_t)sz){perror("read");return 1;}
  fclose(fp);
  const int REC=sizeof(GameInput); int n=sz/REC;
  long steps=0, stay=0, wall=0, oob=0, mate=0, enemy=0, bomb=0, moved=0, newc=0, uround=0, gold=0;
  long stayL[4]={0,0,0,0};
  for(int i=0;i<n;i++){
    const GameInput* in=(const GameInput*)(buf.data()+(long)i*REC);
    GameOutput o=f(in);
    if(o.k!=3||o.order<0||o.order>1||o.vp<0||o.vp>2){printf("ILLEGAL out r=%d k=%d order=%d vp=%d\n",in->round,o.k,o.order,o.vp);return 2;}
    for(int s=0;s<6;s++) if(o.actions[s]<0||o.actions[s]>4){printf("ILLEGAL act r=%d\n",in->round);return 2;}
    for(int u=0;u<2;u++){
      int r=in->my_units[u].row, c=in->my_units[u].col;
      int mr=in->my_units[1-u].row, mc=in->my_units[1-u].col;
      int seen[3][2]; int ns=0, st=0;
      uround++;
      for(int s=0;s<3;s++){
        int a=o.actions[u*3+s]; steps++;
        if(a==4){stay++; st++; continue;}
        int nr=r+DR[a], nc=c+DC[a];
        if(nr<0||nr>16||nc<0||nc>16){oob++; continue;}
        int v=in->grid[nr][nc];
        if(v==-1){wall++; continue;}
        if(nr==mr&&nc==mc){mate++; continue;}
        // 引擎 player_cells_except 把**任一玩家单位**格判为不可进入(含对手) ⇒ 必须一起模拟,
        // 否则会系统性高估「不避开敌格」那一档的收益(它的白扔步与路径偏移都被无视)。
        {bool eb=false; for(int q=0;q<2;q++){ if(in->visible_enemies[q].row==nr&&in->visible_enemies[q].col==nc) eb=true; }
         if(eb){enemy++; continue;}}
        if(v==-3) bomb++;
        moved++;
        bool dup=false; for(int q=0;q<ns;q++) if(seen[q][0]==nr&&seen[q][1]==nc) dup=true;
        if(!dup&&!(nr==in->my_units[u].row&&nc==in->my_units[u].col)){seen[ns][0]=nr;seen[ns][1]=nc;ns++;}
        if(v>0) gold+=(65*v+99)/100;
        r=nr;c=nc;
      }
      newc+=ns; stayL[st]++;
    }
  }
  printf("%-22s rounds=%d uround=%ld new/ur=%.4f stay%%=%.2f%% wall=%ld oob=%ld mate=%ld enemy=%ld bombstep=%ld gold_est=%ld stayhist=[%ld %ld %ld %ld]\n",
     argv[3]?argv[3]:argv[1], n, uround, (double)newc/uround, 100.0*stay/steps, wall, oob, mate, enemy, bomb, gold, stayL[0],stayL[1],stayL[2],stayL[3]);
  return 0;
}
