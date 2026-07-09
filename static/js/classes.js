function getSelectedClass() {
    const selectedElement = document.querySelector('.class-item.selected');
    if (!selectedElement) return null;
    
    const className = selectedElement.querySelector('.class-name').textContent;
    return classes.find(c => c.name === className);
}

// 重绘画布
function loadClasses() {
    fetch('/api/classes')
        .then(response => response.json())
        .then(data => {
            classes = data;
            updateClassList();
            updateWorkflowGuide();
        })
        .catch(error => console.error('加载类别失败:', error));
}

// 更新类别列表
function updateClassList() {
    const classList = document.getElementById('classList');
    classList.innerHTML = '';
    
    classes.forEach((cls, index) => {
        const li = document.createElement('li');
        li.className = 'class-item';
        // 设置CSS变量，用于背景色
        li.style.setProperty('--class-color', cls.color);
        // 显示数字序号（1-9显示数字，超过9显示-）
        const shortcutKey = index < 9 ? (index + 1) : '-';
        li.innerHTML = `
            <span class="class-shortcut">${shortcutKey}</span>
            <span class="class-name">${cls.name}</span>
            <div class="class-actions">
                <button class="class-edit-btn" data-index="${index}">
                    <i class="fas fa-pencil-alt"></i>
                </button>
            </div>
            <button class="class-delete-btn" data-index="${index}">
                <i class="fas fa-times"></i>
            </button>
        `;
        classList.appendChild(li);
    });
    
    // 添加事件监听器
    document.querySelectorAll('.class-item').forEach((item, index) => {
        // 点击选中类别
        item.addEventListener('click', function() {
            document.querySelectorAll('.class-item').forEach(i => i.classList.remove('selected'));
            this.classList.add('selected');
        });
        
        // 编辑按钮事件
        const editBtn = item.querySelector('.class-edit-btn');
        if (editBtn) {
            editBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                editClass(index);
            });
        }
        
        // 删除按钮事件
        const deleteBtn = item.querySelector('.class-delete-btn');
        if (deleteBtn) {
            deleteBtn.addEventListener('click', function(e) {
                e.stopPropagation();
                deleteClass(index);
            });
        }
    });
    
    // 默认选中第一个类别
    const firstClassItem = document.querySelector('.class-item');
    if (firstClassItem) {
        firstClassItem.classList.add('selected');
    }
}

// 添加类别
function addClass() {
    const nameInput = document.getElementById('newClassInput');
    const colorInput = document.getElementById('newClassColor');
    const name = nameInput.value.trim();
    
    if (!name) {
        showToast('请输入标签名称');
        return;
    }
    
    // 检查是否已存在同名类别
    if (classes.some(cls => cls.name === name)) {
        showToast('类别名称已存在');
        return;
    }
    
    const newClass = {
        name: name,
        color: colorInput.value
    };
    
    classes.push(newClass);
    updateClassList();
    saveClasses();
    
    // 清空输入框
    nameInput.value = '';
}

// 编辑类别
function editClass(index) {
    const cls = classes[index];
    document.getElementById('editClassIndex').value = index;
    document.getElementById('editClassName').value = cls.name;
    document.getElementById('editClassColor').value = cls.color;
    
    const modal = document.getElementById('editClassModal');
    modal.style.display = 'block';
}

// 处理类别编辑表单提交
function handleEditClass(e) {
    e.preventDefault();
    
    const index = document.getElementById('editClassIndex').value;
    const name = document.getElementById('editClassName').value.trim();
    const color = document.getElementById('editClassColor').value;
    
    if (!name) {
        showToast('请输入类别名称');
        return;
    }
    
    // 检查是否与其他类别重名
    if (classes.some((cls, i) => i != index && cls.name === name)) {
        showToast('类别名称已存在');
        return;
    }
    
    classes[index] = {
        name: name,
        color: color
    };
    
    updateClassList();
    saveClasses();
    
    // 关闭模态框
    document.getElementById('editClassModal').style.display = 'none';
}

// 删除类别
function deleteClass(index) {
    if (confirm(`确定要删除类别 "${classes[index].name}" 吗？`)) {
        classes.splice(index, 1);
        updateClassList();
        saveClasses();
    }
}

// 保存类别
function saveClasses() {
    fetch('/api/classes', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(classes)
    }).catch(error => console.error('保存类别失败:', error));
}

// 加载图片列表
function getClassColor(className) {
    const cls = classes.find(c => c.name === className);
    return cls ? cls.color : '#ff0000';
}

// 删除标注
function selectClassByIndex(index) {
    if (index < 0 || index >= classes.length) {
        showToast(`标签 ${index + 1} 不存在`);
        return;
    }
    
    // 移除所有选中状态
    document.querySelectorAll('.class-item').forEach(item => {
        item.classList.remove('selected');
    });
    
    // 选中对应的标签
    const classItems = document.querySelectorAll('.class-item');
    if (classItems[index]) {
        classItems[index].classList.add('selected');
        showToast(`已切换到: ${classes[index].name}`);
    }
}

// 删除选中的标注框
