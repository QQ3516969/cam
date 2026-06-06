# CAM Git 结构说明

当前这个项目有两层 git：

## 1. 根仓库

路径：

`/vol2/1000/docker/cam`

用途：

- 代表整个 `cam` 项目
- 记录整体目录结构
- 记录 `control` 当前指向的版本

远端：

- `origin = git@github.com:QQ3516969/cam.git`

当前规范：

- `cam.git` 的 `main` 以根仓库为准
- 以后整个项目对外推送，默认从根仓库推

## 2. control 子仓库

路径：

`/vol2/1000/docker/cam/control`

用途：

- 主要承载控制面板前后端代码
- 可以单独提交，方便做高频 UI / 接口备份

当前状态：

- 仍然是独立 git 仓库
- 现在已经移除了远端 `origin`
- 这样可以避免把它误推到根仓库远端

当前工作方式：

1. 先在 `control` 里提交功能改动
2. 再回到根仓库提交一次，记录 `control` 指针变化
3. 最后从根仓库推送到 `origin/main`

## 3. 远端备份分支

为了避免之前误推的 `control` 历史丢失，远端保留了一个备份分支：

- `control-backup-20260606`

用途：

- 只做历史留档
- 不作为后续主线开发分支

## 4. 以后推荐做法

- 日常改控制面板：先改 `control`
- 改完先提交 `control`
- 然后提交根仓库
- 最后只推根仓库

可以直接用这套顺序：

```bash
cd /vol2/1000/docker/cam/control
git add .
git commit -m "你的功能说明"

cd /vol2/1000/docker/cam
git add control
git commit -m "同步 control 子仓库到某版本"
git push origin main
```
