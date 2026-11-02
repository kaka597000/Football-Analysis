"""Functional capability checks. Synthetic checks are never football KPI evidence."""
import json,os,platform,importlib.metadata as meta
from pathlib import Path
import numpy as np

def main(out):
 import torch,statsmodels.api as sm,optuna,gymnasium as gym
 from stable_baselines3 import PPO
 from scipy.stats import poisson
 torch.manual_seed(42);torch.set_num_threads(1)
 x=torch.randn(32,3);y=x[:,0:1]*2;net=torch.nn.Linear(3,1);optim=torch.optim.Adam(net.parameters(),lr=.05)
 before=float(torch.nn.functional.mse_loss(net(x),y).detach())
 for _ in range(40):optim.zero_grad();loss=torch.nn.functional.mse_loss(net(x),y);loss.backward();optim.step()
 after=float(torch.nn.functional.mse_loss(net(x),y).detach());assert after<before
 rng=np.random.default_rng(42);xx=rng.normal(size=(80,2));yy=rng.poisson(np.exp(.1+.2*xx[:,0]));fit=sm.GLM(yy,sm.add_constant(xx),family=sm.families.Poisson()).fit();assert np.isfinite(fit.predict()).all()
 optuna.logging.set_verbosity(optuna.logging.WARNING);study=optuna.create_study();study.optimize(lambda t:(t.suggest_float('x',-1,1)-.2)**2,n_trials=3);assert len(study.trials)==3
 env=gym.make('CartPole-v1');agent=PPO('MlpPolicy',env,n_steps=32,batch_size=16,n_epochs=1,seed=42,device='cpu',verbose=0);agent.learn(total_timesteps=64);obs,_=env.reset(seed=42);action,_=agent.predict(obs);env.step(action);env.close()
 assert abs(poisson.pmf(np.arange(30),1.5).sum()-1)<1e-8
 packages=['numpy','scipy','scikit-learn','lightgbm','xgboost-cpu','catboost','torch','statsmodels','optuna','gymnasium','stable-baselines3']
 r={'python':platform.python_version(),'cpu_count':os.cpu_count(),'cuda_available':torch.cuda.is_available(),'packages':{p:meta.version(p) for p in packages},'checks':{'torch_optimizer_decreases_loss':True,'statsmodels_poisson_fit':True,'optuna_trials':3,'rl_cartpole_training_steps':64,'poisson_distribution':True},'synthetic_only':True,'football_rl_trained':False,'matlab_required':False}
 Path(out).write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');print(json.dumps(r,ensure_ascii=False))
if __name__=='__main__':
 import sys
 main(sys.argv[1])
